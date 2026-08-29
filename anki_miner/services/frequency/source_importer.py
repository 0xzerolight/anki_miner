"""Import a frequency source (Yomitan zip or plain CSV/TSV) into a per-source index.

A "frequency source" is one rank list the user wants to additively layer with
others. This importer mirrors the dictionary and audio-pack import flows: it
builds a per-source ``index.sqlite`` (plus ``meta.json`` sidecar) under
``<dest_root>/<source_id>/``, staging into a ``.staging-*`` dir and atomically
renaming on success, and copies the original input file alongside the index so a
later "reimport" can re-run without the user re-picking the file.

Two input shapes are supported, dispatched by suffix:

* ``.zip`` — a Yomitan ``frequency`` meta-bank dictionary. BCCWJ-style envelope
  readings are kept in the ``reading`` column; otherwise reading is ``NULL``. On
  a ``(term, reading)`` collision an unmarked row beats a JPDB ㋕ kana-usage row,
  then the smaller (better) rank wins (see :func:`_rank_preference`).
* ``.csv`` / ``.tsv`` / ``.txt`` — a plain rank list. Delimiter is auto-detected,
  a rank/count header declares the numeric direction, and rows are parsed with the shared
  :func:`~anki_miner.services.frequency.csv_parse._extract_word_rank`. A third
  column (``term, reading, rank``) is captured as the reading. First occurrence
  wins per ``(term, reading)`` (matching the legacy CSV loader's semantics).

Occurrence-based sources (larger number = more common) — declared via Yomitan
``frequencyMode`` or detected by :mod:`~anki_miner.services.frequency.mode_probe`
for undeclared zips/CSVs — are auto-converted to real ranks (``1..n``, largest
count first) before storage, so downstream rank filtering/sorting stays correct.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.services._sqlite_index import (
    language_identity,
    prove_owned_slot,
    read_slot_language,
    resolve_auto_store_id,
    resolve_managed_slot,
    write_ownership_marker,
)
from anki_miner.services._staging import promote_staged_dir, repair_managed_slot
from anki_miner.services.frequency import mode_probe, storage
from anki_miner.services.frequency.csv_parse import (
    _extract_word_rank,
    _header_frequency_mode,
    _is_frequency_header,
    _is_word_first_header,
    _normalize_freq_rank_raw,
    _string_to_rank,
    classify_categorical,
    extract_envelope_reading,
    is_kana_usage_display,
    normalize_freq_rank,
)
from anki_miner.services.yomitan_meta_bank import (
    ProgressFn,
    open_yomitan_meta_banks,
)
from anki_miner.utils.csv_utils import detect_delimiter
from anki_miner.utils.robust_fs import robust_rmtree
from anki_miner.utils.slug import slugify

logger = logging.getLogger(__name__)

FREQUENCY_SOURCE_SUFFIXES = (".zip", ".csv", ".tsv", ".txt")
_ZIP_SUFFIXES = frozenset(FREQUENCY_SOURCE_SUFFIXES[:1])
_CSV_SUFFIXES = frozenset(FREQUENCY_SOURCE_SUFFIXES[1:])


def _rank_preference(row: tuple[int, str | None]) -> tuple[bool, int]:
    """Collision sort key for a ``(rank, display_value)`` row.

    A row without JPDB's ㋕ kana-usage marker beats a ㋕-marked row regardless
    of rank (the ㋕ row carries the base word's kana rank, not the spelling's
    own — see :data:`~anki_miner.services.frequency.csv_parse.KANA_USAGE_MARKER`);
    within a bucket the smaller rank wins. For dicts without ㋕ display values
    this reduces exactly to min(rank) with first-wins on ties. A term whose
    ONLY row is ㋕-marked keeps it — nothing better exists to prefer.
    """
    rank, display_value = row
    return (is_kana_usage_display(display_value), rank)


@dataclass(frozen=True)
class FreqSourceImportResult:
    """Outcome of a successful frequency-source import."""

    source_id: str
    source_name: str
    source_revision: str
    format: str
    entry_count: int
    skipped_display_only: int
    # Structurally-malformed meta-bank entries skipped during a zip import
    # (always 0 for CSV/TSV sources). Surfaced to the user so a reduced import
    # doesn't pass unnoticed.
    skipped_malformed: int = 0
    # True when the source was detected/declared occurrence-based and its raw
    # counts were re-ranked to 1..n at import (see mode_probe). Surfaced so the
    # user knows the stored ranks differ from the file's numbers.
    converted_to_ranks: bool = False
    # True when the source is word-based (categorical): its ``freq`` values are
    # level labels ("N5", "Basic") stored display-only and excluded from
    # frequency-rank filtering/sorting. Surfaced so the user knows the source
    # won't affect the rank cutoff (see storage.CATEGORICAL_RANK).
    is_categorical: bool = False


def import_frequency_source(
    input_path: Path,
    dest_root: Path,
    *,
    source_id: str | None = None,
    source_name: str | None = None,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
    overwrite: bool = False,
    before_promote: Callable[[], None] | None = None,
    language: str = "ja",
) -> FreqSourceImportResult:
    """Import ``input_path`` into ``dest_root/<source_id>/index.sqlite``.

    Args:
        input_path: A Yomitan frequency ``.zip`` or a plain ``.csv``/``.tsv``/
            ``.txt`` rank list.
        dest_root: Folder under which ``<source_id>/`` is created (typically
            ``~/.anki_miner/freq_sources/``).
        source_id: Explicit on-disk id. When omitted, derived from the Yomitan
            ``index.json`` title (zip) or the CSV filename stem, then slugified.
        source_name: Explicit human display name. When omitted, a CSV derives it
            from the filename stem. Used by reimport to preserve the existing
            display name instead of re-deriving it from the generic ``source.csv``
            persisted-copy stem. Ignored for zips (their title comes from
            ``index.json``).
        progress: Optional ``(current, total, message)`` callback.
        cancel_check: Optional zero-arg predicate; if it returns True the import
            aborts (partial staging files are cleaned up by the temp dir).
        overwrite: If true, replace an existing same-id source atomically.
        before_promote: Optional last-moment guard run immediately before the
            staged directory replaces the managed slot.
        language: Mining language stamped into the index meta. Defaults to
            ``"ja"``, the pre-transition value for every existing caller.

    Raises:
        SetupError: On a missing/unsupported input, or a source that yields zero
            usable entries, or when the destination exists and overwrite is false.
    """
    if not input_path.exists():
        raise SetupError(f"Frequency source not found: {input_path}")

    suffix = input_path.suffix.lower()
    if suffix in _ZIP_SUFFIXES:
        return _import_zip(
            input_path,
            dest_root,
            source_id=source_id,
            progress=progress,
            cancel_check=cancel_check,
            overwrite=overwrite,
            before_promote=before_promote,
            language=language,
        )
    if suffix in _CSV_SUFFIXES:
        return _import_csv(
            input_path,
            dest_root,
            source_id=source_id,
            source_name=source_name,
            cancel_check=cancel_check,
            overwrite=overwrite,
            before_promote=before_promote,
            language=language,
        )
    raise SetupError(
        f"Unsupported frequency source '{input_path.name}'. Provide a Yomitan .zip or a .csv/.tsv/.txt rank list."
    )


def repair_frequency_source(
    input_path: Path,
    dest_root: Path,
    *,
    source_id: str,
    source_name: str,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> FreqSourceImportResult:
    """Explicitly repair ``source_id``, retaining an invalid prior slot as quarantine."""
    # Read the stamp before the rebuild: repair_managed_slot may quarantine the
    # slot, and a re-import would otherwise fall back to the "ja" default.
    language = read_slot_language(dest_root / source_id)
    return repair_managed_slot(
        input_path,
        dest_root,
        source_id,
        "frequency",
        lambda source, overwrite: import_frequency_source(
            source,
            dest_root,
            source_id=source_id,
            source_name=source_name,
            progress=progress,
            cancel_check=cancel_check,
            overwrite=overwrite,
            language=language,
        ),
    )


def _import_zip(
    zip_path: Path,
    dest_root: Path,
    *,
    source_id: str | None,
    progress: ProgressFn | None,
    cancel_check: Callable[[], bool] | None,
    overwrite: bool,
    before_promote: Callable[[], None] | None,
    language: str,
) -> FreqSourceImportResult:
    with open_yomitan_meta_banks(zip_path, kind="frequency") as banks:
        title = banks.title
        revision = banks.revision
        declared_mode = banks.index.frequency_mode
        resolved_id = source_id or resolve_auto_store_id(
            dest_root,
            _derive_source_id(title),
            "frequency",
            {"source_name": title, "source_revision": revision, **language_identity(language)},
        )

        # Numeric path: key = (term, reading) -> (best rank, display_value),
        # "best" per _rank_preference (non-㋕ beats ㋕, then min rank).
        ranks: dict[tuple[str, str | None], tuple[int, str | None]] = {}
        # Categorical path: key = (term, reading) -> level label (first non-empty
        # wins). Accumulated in the same pass so a word-based source is detected
        # without a second scan; only one path's rows are ultimately stored.
        labels: dict[tuple[str, str | None], str] = {}
        skipped_display_only = 0
        # Categorical-detection signals (see csv_parse.classify_categorical).
        distinct_labels: set[str] = set()
        digit_free_count = 0
        total_labelled = 0
        total_considered = 0

        for bank in banks.iter_banks(progress=progress, cancel_check=cancel_check):
            # Entries are already structurally validated by iter_banks (list,
            # arity >= 3, non-blank term); only the mode/data logic remains.
            for entry in bank:
                if entry[1] != "freq":
                    continue
                term = unicodedata.normalize("NFC", str(entry[0]).strip())
                data = entry[2]
                reading = extract_envelope_reading(data)
                if reading is not None:
                    reading = unicodedata.normalize("NFC", reading)

                # Numeric rank via the existing gate (byte-identical to today,
                # incl. unstripped object-form displayValue); the raw label is
                # tracked separately so a categorical source can be recognised.
                rank, display_value = normalize_freq_rank(data)
                raw_display = _normalize_freq_rank_raw(data)[1]
                label = raw_display.strip() if isinstance(raw_display, str) and raw_display.strip() else None

                if rank is not None:
                    key = (term, reading)
                    candidate = (rank, display_value)
                    existing = ranks.get(key)
                    if existing is None or _rank_preference(candidate) < _rank_preference(existing):
                        ranks[key] = candidate
                else:
                    skipped_display_only += 1

                if label is not None:
                    total_labelled += 1
                    distinct_labels.add(label)
                    if _string_to_rank(label) is None:
                        digit_free_count += 1
                    labels.setdefault((term, reading), label)

                if rank is not None or label is not None:
                    total_considered += 1

        # A declared Yomitan frequencyMode ("occurrence"/"rank") is the author
        # asserting the source is numeric, so it overrides categorical detection
        # (a coarse occurrence dict with few distinct counts would otherwise look
        # categorical). Categorical level dicts leave frequencyMode undeclared.
        is_categorical = not declared_mode and classify_categorical(
            distinct_labels, digit_free_count, total_labelled, total_considered
        )

        if is_categorical:
            # Store labels display-only: the sentinel rank keeps every row out of
            # numeric aggregation; the level shows on the card via display_value.
            # Direction detection is meaningless for categories, so it is skipped.
            rows: Iterable[storage.FreqRow] = (
                (term, reading, storage.CATEGORICAL_RANK, label)
                for (term, reading), label in sorted(labels.items(), key=lambda kv: kv[0])
            )
            entry_count = len(labels)
            skipped_display_only = total_considered - total_labelled  # rank-only rows dropped
            converted = False
        else:
            if not ranks:
                raise SetupError(
                    f"'{title}' yielded no usable frequency entries (skipped "
                    f"{skipped_display_only} display-only entries). "
                    "The dictionary may use an unsupported data format."
                )
            rows, converted = _iter_rank_rows(ranks, declared_mode, language)
            entry_count = len(ranks)

        result = _finalize(
            input_path=zip_path,
            dest_root=dest_root,
            source_id=resolved_id,
            source_name=title,
            source_revision=revision,
            fmt="yomitan-freq",
            rows=rows,
            entry_count=entry_count,
            skipped_display_only=skipped_display_only,
            skipped_malformed=banks.skipped_malformed,
            converted_to_ranks=converted,
            is_categorical=is_categorical,
            cancel_check=cancel_check,
            overwrite=overwrite,
            before_promote=before_promote,
            language=language,
        )

    logger.info(
        "Imported %d frequency entries from '%s' (revision '%s') as source '%s', skipped %d display-only, %d malformed",
        result.entry_count,
        title,
        revision,
        result.source_id,
        skipped_display_only,
        result.skipped_malformed,
    )
    return result


def _import_csv(
    csv_path: Path,
    dest_root: Path,
    *,
    source_id: str | None,
    source_name: str | None = None,
    cancel_check: Callable[[], bool] | None,
    overwrite: bool,
    before_promote: Callable[[], None] | None,
    language: str,
) -> FreqSourceImportResult:
    stem = csv_path.stem
    # Honor an explicit display name (reimport passes the existing meta name);
    # otherwise derive from the filename stem. Preserving it here is what keeps
    # reimport from collapsing the label to the generic "source.csv" stem.
    resolved_name = source_name if source_name else stem
    resolved_id = source_id or resolve_auto_store_id(
        dest_root,
        _derive_source_id(stem),
        "frequency",
        {"source_name": resolved_name, "source_revision": "", **language_identity(language)},
    )

    # key = (term, reading) -> rank; first occurrence wins (matches the legacy
    # CSV loader's semantics, which kept the first rank per word). Plain rank
    # lists carry no Yomitan display string, so display_value is always None here.
    ranks: dict[tuple[str, str | None], int] = {}
    try:
        with open(csv_path, encoding="utf-8") as f:
            sample = f.read(4096)
            f.seek(0)
            delimiter = detect_delimiter(sample)

            import csv as _csv

            reader = _csv.reader(f, delimiter=delimiter)
            first_row = True
            word_first = False
            declared_mode = ""
            for row in reader:
                if cancel_check is not None and cancel_check():
                    raise OperationCancelled("Import cancelled")
                if len(row) < 2:
                    continue
                if first_row:
                    first_row = False
                    if _is_frequency_header(row):
                        word_first = _is_word_first_header(row)
                        declared_mode = _header_frequency_mode(row)
                        continue

                word, rank = _extract_word_rank(row, word_first=word_first)
                if not word or rank is None:
                    continue

                reading = _csv_reading(row, word)
                word = unicodedata.normalize("NFC", word)
                if reading is not None:
                    reading = unicodedata.normalize("NFC", reading)
                key = (word, reading)
                if key not in ranks:
                    ranks[key] = rank
    except OSError as e:
        raise SetupError(f"Error reading frequency source '{csv_path.name}': {e}") from e

    if not ranks:
        raise SetupError(
            f"'{csv_path.name}' yielded no usable frequency entries. "
            "Expected a CSV/TSV with a word column and a numeric rank column. "
            "Word-based / level lists (N5, Basic, 初級) are only supported as "
            "Yomitan .zip dictionaries — import one of those instead."
        )

    # An explicit count/rank header is authoritative. Headerless and ambiguous
    # CSVs still use the statistical probe.
    rows, converted = _iter_rank_rows(ranks, declared_mode, language)

    result = _finalize(
        input_path=csv_path,
        dest_root=dest_root,
        source_id=resolved_id,
        source_name=resolved_name,
        source_revision="",
        fmt="csv",
        rows=rows,
        entry_count=len(ranks),
        skipped_display_only=0,
        converted_to_ranks=converted,
        cancel_check=cancel_check,
        overwrite=overwrite,
        before_promote=before_promote,
        language=language,
    )
    logger.info(
        "Imported %d frequency entries from CSV '%s' as source '%s'",
        result.entry_count,
        csv_path.name,
        result.source_id,
    )
    return result


def _iter_rank_rows(
    ranks: Mapping[tuple[str, str | None], int | tuple[int, str | None]],
    declared_mode: str,
    source_language: str = "ja",
) -> tuple[Iterable[storage.FreqRow], bool]:
    """Yield stored rows in stable order, re-ranking occurrence sources.

    The dedupe mapping remains necessary, but yielded rows stream into SQLite
    instead of duplicating the entire source in a second list.

    ``source_language`` is the language the import stamps into ``meta.json``; it
    selects the probe terms, so a source is only ever steered by its own
    language's list.
    """
    # terms_for_language, NOT mode_probe._terms_for: the latter pools every
    # language's terms for an unknown code, which would let ja decide a ko
    # source's direction. A language with no table contributes no terms, so
    # term_values stays empty and probe_direction's own pooling fallback finds
    # nothing to look up — the decision falls through to rank-based. Widening
    # this set would re-open exactly that miscall.
    probe_terms = {
        term
        for table in (mode_probe.MORE_COMMON_TERMS, mode_probe.LESS_COMMON_TERMS)
        for term in mode_probe.terms_for_language(table, source_language)
    }
    term_values: dict[str, list[int]] = {}
    for (term, _reading), value in ranks.items():
        if term in probe_terms:
            rank = value if isinstance(value, int) else value[0]
            term_values.setdefault(term, []).append(rank)

    if mode_probe.resolve_is_occurrence(declared_mode, term_values, source_language):
        ordered = sorted(
            ranks.items(),
            key=lambda item: (
                -(item[1] if isinstance(item[1], int) else item[1][0]),
                item[0][0],
                item[0][1] or "",
            ),
        )
        rows = (
            (term, reading, new_rank, None if isinstance(value, int) else value[1])
            for new_rank, ((term, reading), value) in enumerate(ordered, 1)
        )
        return rows, True

    ordered = sorted(ranks.items(), key=lambda item: item[1] if isinstance(item[1], int) else item[1][0])
    rows = (
        (
            term,
            reading,
            value if isinstance(value, int) else value[0],
            None if isinstance(value, int) else value[1],
        )
        for (term, reading), value in ordered
    )
    return rows, False


def _csv_reading(row: list[str], word: str) -> str | None:
    """Return a reading from a ``term, reading, rank`` row, else ``None``.

    Only a 3+-column row whose col-0 is the matched word carries a reading; the
    reading is col-1. If col-1 is empty or numeric (i.e. the file is really a
    ``word, rank`` 2-col list padded with a blank), no reading is captured.
    """
    if len(row) < 3:
        return None
    if not row or row[0].strip() != word:
        return None
    candidate = row[1].strip()
    if not candidate:
        return None
    # A purely-numeric col-1 is the rank, not a reading (the rank scan in
    # _extract_word_rank already consumed it).
    try:
        int(candidate)
        return None
    except ValueError:
        return candidate


def _finalize(
    *,
    input_path: Path,
    dest_root: Path,
    source_id: str,
    source_name: str,
    source_revision: str,
    fmt: str,
    rows: Iterable[storage.FreqRow],
    entry_count: int,
    skipped_display_only: int,
    skipped_malformed: int = 0,
    converted_to_ranks: bool = False,
    is_categorical: bool = False,
    cancel_check: Callable[[], bool] | None,
    overwrite: bool,
    before_promote: Callable[[], None] | None,
    language: str,
) -> FreqSourceImportResult:
    """Build the index under a staging dir, then atomically promote it.

    Copies the original input alongside ``index.sqlite`` (``source.zip`` /
    ``source.csv``) for later reimport.
    """
    try:
        final_path = resolve_managed_slot(dest_root, source_id)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(final_path):
        if not overwrite:
            raise SetupError(f"Frequency source '{source_id}' already exists")
        if not prove_owned_slot(final_path.parent, source_id, "frequency"):
            raise SetupError(
                f"Frequency source '{source_id}' exists but is not an Anki Miner-managed frequency source; "
                "refusing to overwrite it"
            )

    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=final_path.parent))
    try:
        write_ownership_marker(staging, source_id, "frequency")
        db_path = staging / "index.sqlite"
        meta = {
            "schema_version": str(storage.SCHEMA_VERSION),
            "format": fmt,
            "source_name": source_name,
            "source_revision": source_revision,
            "import_date": datetime.now(UTC).isoformat(),
            "entry_count": str(entry_count),
            # "1"/"0" (not bool) — read back with an explicit == "1" compare so a
            # stored "0" never coerces truthy (bool("0") is True).
            "is_categorical": "1" if is_categorical else "0",
            "language": language,
        }
        storage.build_index(db_path, rows, meta)

        # Persist the source file so a later "reimport" can rebuild without the
        # user re-picking it (mirrors the dict importer's source.zip).
        source_copy_name = "source" + input_path.suffix.lower()
        shutil.copy2(input_path, staging / source_copy_name)

        if cancel_check is not None and cancel_check():
            raise OperationCancelled("Import cancelled")

        try:
            promote_staged_dir(
                staging,
                final_path,
                mover=shutil.move,
                overwrite=overwrite,
                before_promote=before_promote,
            )
        except FileExistsError as exc:
            raise SetupError(f"Frequency source '{source_id}' already exists") from exc
    finally:
        # On success the staging dir was moved away; clean up on any failure
        # so a partial import does not orphan a .staging-* dir in dest_root.
        robust_rmtree(staging, mode="outcome")

    return FreqSourceImportResult(
        source_id=source_id,
        source_name=source_name,
        source_revision=source_revision,
        format=fmt,
        entry_count=entry_count,
        skipped_display_only=skipped_display_only,
        skipped_malformed=skipped_malformed,
        converted_to_ranks=converted_to_ranks,
        is_categorical=is_categorical,
    )


def _derive_source_id(name: str) -> str:
    """Slugify a title / filename stem into an on-disk source id.

    Mirrors the dictionary importer's slug rule: lowercase ASCII, non-ASCII
    code points become ``u<hex>``, runs of other chars collapse to ``-``.
    """
    return _slug(name)


def _slug(text: str) -> str:
    """ASCII slug suitable for a directory name. CJK falls through as hex codepoints."""
    return slugify(text, fallback="source")

"""Shared scaffolding for Yomitan ``term_meta_bank`` importers.

Yomitan frequency and pitch-accent dictionaries ship as a zip containing
``index.json`` plus one or more ``term_meta_bank_*.json`` files, each a flat
JSON array of ``[term, mode, data]`` triples. The frequency and pitch importers
diverge only in per-entry normalization (which ``mode`` they keep and how they
shape ``data``) and their output CSV columns. Everything else — safe zip
extraction, index validation, the strict ``format == 3`` gate, the per-file
progress/cancel loop, and the atomic CSV write — is identical and lives here.

Both importers require **exactly** ``format == 3`` (the term/definition importer
accepts ``>= 3``); that stricter gate is preserved here verbatim.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.schema_validation import (
    ensure_bank_array,
    is_valid_meta_bank_entry,
)
from anki_miner.services.dictionary.zip_safety import raise_if_index_nested, validate_zip_safe

logger = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]


@dataclass(frozen=True)
class YomitanMetaIndex:
    """Validated ``index.json`` metadata common to meta-bank dictionaries."""

    title: str
    revision: str
    frequency_mode: str


@dataclass
class YomitanMetaBanks:
    """Handle over an extracted Yomitan meta-bank zip.

    Carries the validated :class:`YomitanMetaIndex` and exposes
    :meth:`iter_banks`, which walks the ``term_meta_bank_*.json`` files in
    sorted order with progress + cancellation. Only valid while the owning
    :func:`open_yomitan_meta_banks` context manager is active (the extraction
    temp dir is cleaned on exit).
    """

    index: YomitanMetaIndex
    _meta_files: list[Path]
    # Structurally-malformed entries dropped during :meth:`iter_banks`. Read by
    # the importers *after* the generator is exhausted and surfaced to the user.
    skipped_malformed: int = 0

    @property
    def title(self) -> str:
        return self.index.title

    @property
    def revision(self) -> str:
        return self.index.revision

    def iter_banks(
        self,
        *,
        progress: ProgressFn | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterator[list[Any]]:
        """Yield each meta-bank's *structurally-valid* entries, one file at a time.

        Structurally-malformed entries (not a list, arity < 3, or a blank term)
        are dropped and tallied on :attr:`skipped_malformed` so the caller can
        surface the count — mode/data validity remains the importer's concern. A
        bank file whose top-level JSON is not an array raises (wholly unreadable).

        Fires ``progress(file_idx, total, message)`` after each file and
        raises ``SetupError("Import cancelled")`` if ``cancel_check`` returns
        True between files (the existing CSV is left untouched by the caller's
        atomic write). A final ``progress(total, total, "Done")`` is emitted
        once all files are consumed.
        """
        total = len(self._meta_files)
        for file_idx, meta_file in enumerate(self._meta_files, 1):
            if cancel_check and cancel_check():
                raise SetupError("Import cancelled")
            try:
                bank = json.loads(meta_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise SetupError(f"Invalid {meta_file.name}: {e}") from e
            bank = ensure_bank_array(bank, meta_file.name)

            valid: list[Any] = []
            for entry in bank:
                if is_valid_meta_bank_entry(entry):
                    valid.append(entry)
                else:
                    self.skipped_malformed += 1

            yield valid

            if progress:
                progress(file_idx, total, f"Imported {meta_file.name}")

        if progress:
            progress(total, total, "Done")


@contextmanager
def open_yomitan_meta_banks(
    zip_path: Path,
    *,
    kind: str,
) -> Iterator[YomitanMetaBanks]:
    """Extract and validate a Yomitan meta-bank zip, yielding a handle.

    Performs the shared validation both importers need: existence, safe
    extraction, ``index.json`` parse, required ``title``, the strict
    ``format == 3`` gate, and presence of at least one ``term_meta_bank_*.json``.

    Args:
        zip_path: Path to the Yomitan-format zip.
        kind: Human label for error messages — ``"pitch"`` or ``"frequency"``.
            Drives the "no meta banks" / "no usable entries" wording so each
            importer keeps its existing, distinct guidance.

    Yields:
        A :class:`YomitanMetaBanks` valid for the duration of the ``with`` block.

    Raises:
        SetupError: on any invalid input (missing zip, corrupt zip, unsafe
            paths, missing/invalid index.json, missing title, unsupported
            format version, or missing meta banks).
    """
    if not zip_path.exists():
        raise SetupError(f"Yomitan {kind} zip not found: {zip_path}")

    with tempfile.TemporaryDirectory(prefix=f"anki_miner_yomitan_{kind}_") as tmp:
        tmp_path = Path(tmp)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                validate_zip_safe(zf, tmp_path)
                zf.extractall(tmp_path)
        except zipfile.BadZipFile as e:
            raise SetupError(f"Corrupt zip file: {e}") from e

        index_file = tmp_path / "index.json"
        if not index_file.exists():
            nested = [str(p.relative_to(tmp_path)) for p in tmp_path.rglob("index.json")]
            raise_if_index_nested(nested, missing_msg="Zip missing required index.json")

        try:
            raw_index = json.loads(index_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SetupError(f"Invalid index.json: {e}") from e

        title = str(raw_index.get("title", "")).strip()
        revision = str(raw_index.get("revision", "")).strip()
        if not title:
            raise SetupError("index.json missing required 'title'")

        # Yomitan format v1/v2 use different term_meta_bank schemas than v3.
        # A v1/v2 zip would parse silently and could yield zero rows or wrong
        # data. Strict equality with 3 surfaces the mismatch clearly; revisit
        # if/when Yomitan ships a v4 we want to accept. (Both meta-bank
        # importers gate on == 3, unlike the term importer's >= 3.)
        format_version = raw_index.get("format")
        if format_version != 3:
            raise SetupError(
                f"'{title}' uses unsupported Yomitan format version {format_version!r}. "
                "anki_miner supports format version 3 only. "
                "Re-download from a current Yomitan source."
            )

        frequency_mode = str(raw_index.get("frequencyMode", "")).strip()

        meta_files = sorted(tmp_path.glob("term_meta_bank_*.json"))
        if not meta_files:
            raise SetupError(
                f"Zip contains no {kind} data (term_meta_bank_*.json missing). "
                "This is likely a definition-only dictionary; import it via "
                "Settings → Dictionary → Add Dictionary instead."
            )

        yield YomitanMetaBanks(
            index=YomitanMetaIndex(title=title, revision=revision, frequency_mode=frequency_mode),
            _meta_files=meta_files,
        )


def atomic_write_csv(dest_csv: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    """Write ``header`` + ``rows`` to ``dest_csv`` atomically.

    Stages to a sibling ``.tmp`` then ``os.replace`` so a crash mid-write
    leaves the user's existing CSV intact. The ``.tmp`` is unlinked in a
    ``finally`` so a failure mid-rows doesn't orphan it in ~/.anki_miner
    (carries forward the T-40 fix into the shared writer).
    """
    dest_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_csv.with_suffix(dest_csv.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for row in rows:
                writer.writerow(row)
        os.replace(tmp_path, dest_csv)
    finally:
        # On success os.replace already consumed the temp; missing_ok makes
        # this a no-op then, and cleans the orphan on a mid-rows failure.
        tmp_path.unlink(missing_ok=True)

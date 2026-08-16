"""JMdict XML -> SQLite index importer.

Security / threat model
-----------------------
JMdict is a single-source format distributed by EDRDG, but the file path comes
from the user, so the input is treated as semi-trusted. Stdlib
``xml.etree.ElementTree`` is **not** hardened against XXE or billion-laughs
attacks; ``defusedxml`` is not a project dependency at the time of writing.
The trade-off is accepted because:

* JMdict ships as a self-contained file with no DOCTYPE / external entity
  declarations in the canonical EDRDG release.
* The importer is invoked manually from the GUI by the same user who chose
  the file, not from arbitrary network input.

If JMdict ever starts shipping with external entities, or the importer is
ever wired up to fetch XML directly, swap ``xml.etree.ElementTree`` for
``defusedxml.ElementTree`` here.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET  # noqa: S405 - see module docstring
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Callable, Iterator

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.services._sqlite_index import (
    prove_owned_slot,
    resolve_managed_slot,
    write_ownership_marker,
)
from anki_miner.services._staging import promote_staged_dir, repair_managed_slot
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    create_lookup_indexes,
    write_meta,
)
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

JMDICT_DICT_ID = "jmdict-english"

# Cap senses per entry on the card back; keeps cards scannable.
MAX_SENSES = 5

ProgressFn = Callable[[int, int, str], None]
_Sense = tuple[list[str], set[str], set[str]]


@dataclass(frozen=True)
class JMdictImportResult:
    dict_id: str
    entry_count: int


def import_jmdict_xml(
    xml_path: Path,
    dest_root: Path,
    *,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
    overwrite: bool = True,
) -> JMdictImportResult:
    """Import JMdict XML into ``dest_root/jmdict-english/index.sqlite``.

    Overwrites by default; startup migration uses no-clobber publication.
    """
    logger.info("JMdict import: source=%s", xml_path.name)
    if not xml_path.exists():
        raise SetupError(f"JMdict XML not found: {xml_path}")

    try:
        final = resolve_managed_slot(dest_root, JMDICT_DICT_ID)
    except ValueError as exc:
        logger.warning(
            "JMdict import failed: stage=resolve exc=%s",
            type(exc).__name__,
        )
        raise SetupError(str(exc)) from exc
    if os.path.lexists(final):
        if not overwrite:
            raise SetupError(f"Dictionary '{JMDICT_DICT_ID}' already exists")
        if not prove_owned_slot(final.parent, JMDICT_DICT_ID, "dictionary"):
            raise SetupError(
                f"Dictionary '{JMDICT_DICT_ID}' exists but is not an Anki Miner-managed dictionary; "
                "refusing to overwrite it"
            )

    try:
        tree = ET.parse(str(xml_path))  # noqa: S314 - see module docstring
    except ET.ParseError as e:
        logger.warning(
            "JMdict import failed: stage=parse exc=%s",
            type(e).__name__,
        )
        raise SetupError(f"Failed to parse JMdict XML: {e}") from e

    root = tree.getroot()
    entries = list(root.findall("entry"))
    total_entries = len(entries)
    malformed_entries = 0
    malformed_exemplar: str | int = "-"

    with tempfile.TemporaryDirectory(prefix="anki_miner_jmdict_") as tmp:
        staging = Path(tmp) / JMDICT_DICT_ID
        staging.mkdir(parents=True, exist_ok=True)
        write_ownership_marker(staging, JMDICT_DICT_ID, "dictionary")
        db_path = staging / "index.sqlite"
        # Tables only: the lookup indexes are built once after the rows land.
        create_index(db_path, with_lookup_indexes=False)

        def rows() -> Iterator[DictRow]:
            nonlocal malformed_entries, malformed_exemplar
            for i, entry in enumerate(entries, 1):
                if cancel_check and cancel_check():
                    raise OperationCancelled("Import cancelled")

                ent_seq = entry.findtext("ent_seq")
                sequence = int(ent_seq) if ent_seq and ent_seq.isdigit() else None

                terms: list[str] = []
                for k in entry.findall("k_ele"):
                    keb = k.findtext("keb")
                    if keb:
                        terms.append(keb)

                readings: list[str] = []
                # Parallel to ``readings``: the kanji headwords each reading is
                # restricted to via ``re_restr``. Empty list = applies to all
                # kanji headwords.
                reading_restrs: list[list[str]] = []
                for r in entry.findall("r_ele"):
                    reb = r.findtext("reb")
                    if reb:
                        readings.append(reb)
                        reading_restrs.append([x.text for x in r.findall("re_restr") if x.text])

                senses: list[_Sense] = []
                for sense in entry.findall("sense"):
                    glosses = [g.text for g in sense.findall("gloss") if g.text]
                    if glosses:
                        senses.append(
                            (
                                glosses,
                                {x.text for x in sense.findall("stagk") if x.text},
                                {x.text for x in sense.findall("stagr") if x.text},
                            )
                        )
                if not senses:
                    malformed_entries += 1
                    if malformed_exemplar == "-":
                        malformed_exemplar = sequence if sequence is not None else "unknown-sequence"
                    continue

                if not terms and not readings:
                    malformed_entries += 1
                    if malformed_exemplar == "-":
                        malformed_exemplar = sequence if sequence is not None else "unknown-sequence"

                # One kanji-keyed row per APPLICABLE reading, respecting
                # ``re_restr``: a reading with no restriction applies to every
                # kanji headword; a restricted reading pairs only with its
                # permitted kanji. Attesting every applicable reading against
                # the kanji headword lets reading-scoped lookups of secondary
                # readings (e.g. 行く/ゆく) still match and earn the reading boost.
                for term in terms:
                    applicable = [
                        reb
                        for reb, restrs in zip(readings, reading_restrs, strict=True)
                        if not restrs or term in restrs
                    ]
                    if applicable:
                        for reb in applicable:
                            content = _format_senses_for_row(senses, term, reb)
                            if content:
                                yield DictRow(
                                    term=term,
                                    reading=reb,
                                    content=content,
                                    sequence=sequence,
                                )
                    else:
                        # No reading applies to this kanji headword (unusual);
                        # still emit the term so its definition remains lookable.
                        content = _format_senses_for_row(senses, term, None)
                        if content:
                            yield DictRow(
                                term=term,
                                reading=None,
                                content=content,
                                sequence=sequence,
                            )

                # One row per reading, keyed by the reading (term and reading equal).
                for reading in readings:
                    content = _format_senses_for_row(senses, None, reading)
                    if content:
                        yield DictRow(
                            term=reading,
                            reading=reading,
                            content=content,
                            sequence=sequence,
                        )

                if progress and i % 1000 == 0:
                    progress(i, total_entries, f"Processed {i}/{total_entries} entries")

        row_count = bulk_insert(db_path, rows())
        # Deferred to here so the load did not maintain two B-trees per insert.
        create_lookup_indexes(db_path)

        if malformed_entries:
            log_summary(
                logger,
                "JMdict rows dropped",
                level=logging.WARNING,
                count=malformed_entries,
                exemplar=malformed_exemplar,
            )

        if cancel_check and cancel_check():
            raise OperationCancelled("Import cancelled")

        write_meta(
            db_path,
            {
                "schema_version": str(SCHEMA_VERSION),
                "format": "jmdict",
                "source_name": "JMdict (English)",
                "source_revision": "",
                "import_date": datetime.now(UTC).isoformat(),
                "entry_count": str(row_count),
            },
        )

        if cancel_check and cancel_check():
            raise OperationCancelled("Import cancelled")

        final.parent.mkdir(parents=True, exist_ok=True)

        if cancel_check and cancel_check():
            raise OperationCancelled("Import cancelled")
        promote_staged_dir(staging, final, mover=shutil.move, overwrite=overwrite)

        if progress:
            progress(total_entries, total_entries, "Done")

        log_summary(
            logger,
            "JMdict import done",
            source=xml_path,
            source_entries=total_entries,
            entries=row_count,
            malformed=malformed_entries,
        )
        return JMdictImportResult(dict_id=JMDICT_DICT_ID, entry_count=row_count)


def repair_jmdict_xml(
    xml_path: Path,
    dest_root: Path,
    *,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> JMdictImportResult:
    """Explicitly repair JMdict, retaining an invalid prior slot as quarantine."""
    return repair_managed_slot(
        xml_path,
        dest_root,
        JMDICT_DICT_ID,
        "dictionary",
        lambda source, overwrite: import_jmdict_xml(
            source,
            dest_root,
            progress=progress,
            cancel_check=cancel_check,
            overwrite=overwrite,
        ),
    )


def _format_senses_html(senses: list[list[str]]) -> str:
    items = "".join(f"<li>{escape('; '.join(glosses))}</li>" for glosses in senses[:MAX_SENSES])
    return f"<ol>{items}</ol>"


def _format_senses_for_row(senses: list[_Sense], term: str | None, reading: str | None) -> str:
    applicable = [
        glosses
        for glosses, restricted_terms, restricted_readings in senses
        if (not restricted_terms or term in restricted_terms)
        and (not restricted_readings or reading in restricted_readings)
    ]
    if not applicable:
        return ""
    return _format_senses_html(applicable)

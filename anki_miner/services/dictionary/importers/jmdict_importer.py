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
import shutil
import tempfile
import xml.etree.ElementTree as ET  # noqa: S405 - see module docstring
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Callable, Iterator

from anki_miner.exceptions import SetupError
from anki_miner.services._staging import promote_staged_dir
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)

logger = logging.getLogger(__name__)

JMDICT_DICT_ID = "jmdict-english"

# Cap senses per entry on the card back; keeps cards scannable.
MAX_SENSES = 5

ProgressFn = Callable[[int, int, str], None]


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
) -> JMdictImportResult:
    """Import JMdict XML into ``dest_root/jmdict-english/index.sqlite``.

    Always overwrites the target — JMdict only has one canonical dict_id.
    """
    if not xml_path.exists():
        raise SetupError(f"JMdict XML not found: {xml_path}")

    try:
        tree = ET.parse(str(xml_path))  # noqa: S314 - see module docstring
    except ET.ParseError as e:
        raise SetupError(f"Failed to parse JMdict XML: {e}") from e

    root = tree.getroot()
    entries = list(root.findall("entry"))
    total_entries = len(entries)

    with tempfile.TemporaryDirectory(prefix="anki_miner_jmdict_") as tmp:
        staging = Path(tmp) / JMDICT_DICT_ID
        staging.mkdir(parents=True, exist_ok=True)
        db_path = staging / "index.sqlite"
        create_index(db_path)

        def rows() -> Iterator[DictRow]:
            for i, entry in enumerate(entries, 1):
                if cancel_check and cancel_check():
                    raise SetupError("Import cancelled")

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

                senses: list[list[str]] = []
                for sense in entry.findall("sense"):
                    glosses = [g.text for g in sense.findall("gloss") if g.text]
                    if glosses:
                        senses.append(glosses)
                if not senses:
                    continue

                content = _format_senses_html(senses)

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
                            yield DictRow(
                                term=term,
                                reading=reb,
                                content=content,
                                sequence=sequence,
                            )
                    else:
                        # No reading applies to this kanji headword (unusual);
                        # still emit the term so its definition remains lookable.
                        yield DictRow(
                            term=term,
                            reading=None,
                            content=content,
                            sequence=sequence,
                        )

                # One row per reading, keyed by the reading (term and reading equal).
                for reading in readings:
                    yield DictRow(
                        term=reading,
                        reading=reading,
                        content=content,
                        sequence=sequence,
                    )

                if progress and i % 1000 == 0:
                    progress(i, total_entries, f"Processed {i}/{total_entries} entries")

        row_count = bulk_insert(db_path, rows())

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

        dest_root.mkdir(parents=True, exist_ok=True)
        final = dest_root / JMDICT_DICT_ID

        # JMdict has one canonical dict_id, so always overwrite.
        promote_staged_dir(staging, final, mover=shutil.move, overwrite=True)

        if progress:
            progress(total_entries, total_entries, "Done")

        return JMdictImportResult(dict_id=JMDICT_DICT_ID, entry_count=row_count)


def _format_senses_html(senses: list[list[str]]) -> str:
    items = "".join(f"<li>{escape('; '.join(glosses))}</li>" for glosses in senses[:MAX_SENSES])
    return f"<ol>{items}</ol>"

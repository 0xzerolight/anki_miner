"""CSV/TSV dictionary -> SQLite index importer.

One entry per row. ``.csv`` is comma-separated and ``.tsv`` tab-separated, both
UTF-8 (a byte-order mark is accepted). Columns:

* with a header row, by name: the term column is ``term``/``word``/
  ``expression``/``headword``/``front``, the definition column is
  ``definition``/``meaning``/``gloss``/``glossary``/``translation``/``back``,
  and an optional ``reading``/``pronunciation``/``kana``/``pinyin`` column;
* without one, by position: two columns are term and definition, three or
  more are term, reading and definition (later columns are ignored).

Definitions are plain text: line breaks are kept and markup is shown as typed.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from anki_miner.exceptions import SetupError, raise_if_cancelled
from anki_miner.services._sqlite_index import read_slot_language
from anki_miner.services._staging import repair_managed_slot
from anki_miner.services.dictionary.importers.glossary_markup import glossary_row, text_nodes
from anki_miner.services.dictionary.importers.plain_index import (
    ProgressFn,
    plain_dict_id,
    write_plain_index,
)
from anki_miner.services.dictionary.storage import DictRow
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

CSV_SUFFIXES = (".csv", ".tsv")
_TERM_HEADERS = frozenset({"term", "word", "expression", "headword", "front"})
_READING_HEADERS = frozenset({"reading", "pronunciation", "kana", "pinyin"})
_DEFINITION_HEADERS = frozenset({"definition", "meaning", "gloss", "glossary", "translation", "back"})


@dataclass(frozen=True)
class CsvImportResult:
    dict_id: str
    source_name: str
    entry_count: int
    skipped_malformed: int


def _columns(header: list[str]) -> tuple[int, int | None, int] | None:
    """``(term, reading, definition)`` column indexes named by a header row, else None."""
    names = [cell.strip().casefold() for cell in header]
    term = next((i for i, name in enumerate(names) if name in _TERM_HEADERS), None)
    definition = next((i for i, name in enumerate(names) if name in _DEFINITION_HEADERS), None)
    if term is None or definition is None:
        return None
    reading = next((i for i, name in enumerate(names) if name in _READING_HEADERS), None)
    return term, reading, definition


def _cell(row: list[str], index: int | None) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def import_csv_dictionary(
    source: Path,
    dest_root: Path,
    *,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
    overwrite: bool = False,
    dict_id: str | None = None,
    before_promote: Callable[[], None] | None = None,
    language: str = "ja",
) -> CsvImportResult:
    """Import a ``.csv``/``.tsv`` file into ``dest_root/<dict_id>/index.sqlite``.

    The dictionary is named after the file (``my-words.tsv`` -> ``my-words``);
    ``dict_id`` pins the slot (Re-import), otherwise it derives from that name.
    """
    suffix = source.suffix.lower()
    if suffix not in CSV_SUFFIXES:
        raise SetupError(f"Not a .csv or .tsv file: {source.name}")
    if not source.is_file():
        raise SetupError(f"Dictionary file not found: {source}")
    title = source.stem
    slot = dict_id or plain_dict_id(dest_root, title, language)
    log_summary(logger, "CSV dictionary import", source=source, dict_id=slot)
    skipped = 0

    def rows() -> Iterator[DictRow]:
        nonlocal skipped
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t" if suffix == ".tsv" else ",")
            columns: tuple[int, int | None, int] | None = None
            try:
                for line_no, row in enumerate(reader, 1):
                    if line_no % 1000 == 0:
                        raise_if_cancelled(cancel_check)
                    if line_no == 1:
                        columns = _columns(row)
                        if columns is not None:
                            continue
                    if columns is not None:
                        term_i, reading_i, definition_i = columns
                    elif len(row) >= 3:
                        term_i, reading_i, definition_i = 0, 1, 2
                    else:
                        term_i, reading_i, definition_i = 0, None, 1
                    term, definition = _cell(row, term_i), _cell(row, definition_i)
                    content = glossary_row([text_nodes(definition)]) if term else ""
                    if not content:
                        if any(cell.strip() for cell in row):
                            skipped += 1
                        continue
                    yield DictRow(term=term, reading=_cell(row, reading_i) or None, content=content)
            except UnicodeDecodeError as exc:
                raise SetupError(f"{source.name} is not UTF-8 text") from exc
            except csv.Error as exc:
                raise SetupError(f"{source.name}, line {reader.line_num}: {exc}") from exc

    count = write_plain_index(
        rows(),
        dest_root=dest_root,
        dict_id=slot,
        title=title,
        format_name="csv",
        language=language,
        keep={source.name: source},
        overwrite=overwrite,
        progress=progress,
        cancel_check=cancel_check,
        before_promote=before_promote,
    )
    if skipped:
        log_summary(logger, "CSV dictionary rows skipped", level=logging.WARNING, dict_id=slot, count=skipped)
    log_summary(logger, "CSV dictionary import done", dict_id=slot, entries=count, skipped=skipped)
    return CsvImportResult(dict_id=slot, source_name=title, entry_count=count, skipped_malformed=skipped)


def repair_csv_dictionary(
    source: Path,
    dest_root: Path,
    *,
    dict_id: str,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> CsvImportResult:
    """Rebuild one slot from its kept source, quarantining an invalid prior slot."""
    # Read before the rebuild: a quarantined slot would otherwise come back "ja".
    language = read_slot_language(dest_root / dict_id)
    return repair_managed_slot(
        source,
        dest_root,
        dict_id,
        "dictionary",
        lambda path, overwrite: import_csv_dictionary(
            path,
            dest_root,
            progress=progress,
            cancel_check=cancel_check,
            overwrite=overwrite,
            dict_id=dict_id,
            language=language,
        ),
    )

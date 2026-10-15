"""CSV/TSV dictionaries import into an indexed slot (F-17)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.csv_importer import (
    CsvImportResult,
    import_csv_dictionary,
    repair_csv_dictionary,
)
from anki_miner.services.dictionary.importers.plain_index import SOURCE_DIR
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.storage import open_readonly, read_meta


def _lookup(dest_root: Path, dict_id: str, language: str, word: str) -> str | None:
    provider = IndexedDictProvider(dict_id, dest_root / dict_id / "index.sqlite", keys=get_profile(language).dict_keys)
    provider.load()
    try:
        return provider.lookup(word)
    finally:
        provider.close()


def _rows(db: Path) -> list[tuple[str, str | None, str]]:
    conn = open_readonly(db)
    try:
        return list(conn.execute("SELECT term, reading, content FROM entries ORDER BY id"))
    finally:
        conn.close()


def test_two_column_csv_imports_term_and_definition(tmp_path: Path):
    source = tmp_path / "spanish-words.csv"
    source.write_text('casa,house\nperro,"dog\nhound"\n', encoding="utf-8")

    result = import_csv_dictionary(source, tmp_path / "dicts", language="es")

    assert result == CsvImportResult(
        dict_id="spanish-words", source_name="spanish-words", entry_count=2, skipped_malformed=0
    )
    slot = tmp_path / "dicts" / "spanish-words"
    assert read_meta(slot / "index.sqlite")["format"] == "csv"
    assert (slot / SOURCE_DIR / "spanish-words.csv").is_file()
    definition = _lookup(tmp_path / "dicts", "spanish-words", "es", "Perro")
    assert definition is not None and "dog<br>hound" in definition


def test_three_column_tsv_carries_a_reading(tmp_path: Path):
    source = tmp_path / "zh.tsv"
    source.write_text("\ufeff中国\tzhōng guó\tChina\n", encoding="utf-8")

    import_csv_dictionary(source, tmp_path / "dicts", language="zh")

    [(term, reading, content)] = _rows(tmp_path / "dicts" / "zh" / "index.sqlite")
    assert term == "中国"
    assert reading is not None and "zh" in reading
    assert "China" in content


def test_a_header_row_maps_columns_by_name(tmp_path: Path):
    source = tmp_path / "deck.csv"
    source.write_text("Notes,Meaning,Word\nx,house,casa\n", encoding="utf-8")

    result = import_csv_dictionary(source, tmp_path / "dicts", language="es")

    [(term, reading, content)] = _rows(tmp_path / "dicts" / "deck" / "index.sqlite")
    assert result.entry_count == 1
    assert (term, reading) == ("casa", None)
    assert "house" in content


def test_markup_in_a_definition_is_shown_as_typed(tmp_path: Path):
    source = tmp_path / "m.csv"
    source.write_text('casa,"<b>house</b>"\n', encoding="utf-8")

    import_csv_dictionary(source, tmp_path / "dicts", language="es")

    [(_term, _reading, content)] = _rows(tmp_path / "dicts" / "m" / "index.sqlite")
    assert "&lt;b&gt;house&lt;/b&gt;" in content


def test_rows_without_term_or_definition_are_counted_and_skipped(tmp_path: Path):
    source = tmp_path / "gaps.csv"
    source.write_text("casa,house\n,orphan\nsolo\n\nperro,dog\n", encoding="utf-8")

    result = import_csv_dictionary(source, tmp_path / "dicts", language="es")

    assert result.entry_count == 2
    assert result.skipped_malformed == 2


def test_a_file_that_is_not_utf8_is_refused(tmp_path: Path):
    source = tmp_path / "latin1.csv"
    source.write_bytes("caña,cane\n".encode("latin-1"))

    with pytest.raises(SetupError, match="not UTF-8"):
        import_csv_dictionary(source, tmp_path / "dicts", language="es")
    assert not (tmp_path / "dicts" / "latin1").exists()


def test_other_suffixes_are_refused(tmp_path: Path):
    source = tmp_path / "words.txt"
    source.write_text("casa,house\n", encoding="utf-8")

    with pytest.raises(SetupError, match=".csv or .tsv"):
        import_csv_dictionary(source, tmp_path / "dicts", language="es")


def test_repair_rebuilds_from_the_kept_copy_with_its_language_and_name(tmp_path: Path):
    source = tmp_path / "words.csv"
    source.write_text("casa,house\n", encoding="utf-8")
    dest_root = tmp_path / "dicts"
    import_csv_dictionary(source, dest_root, language="es")
    source.unlink()

    result = repair_csv_dictionary(dest_root / "words" / SOURCE_DIR / "words.csv", dest_root, dict_id="words")

    assert result.source_name == "words"
    assert result.entry_count == 1
    assert read_meta(dest_root / "words" / "index.sqlite")["language"] == "es"

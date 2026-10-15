"""The shared writer and kept-source lookup behind the StarDict and CSV importers (F-17)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.services._sqlite_index import read_ownership_marker, read_slot_language
from anki_miner.services.dictionary.importers.glossary_markup import glossary_row, text_nodes
from anki_miner.services.dictionary.importers.plain_index import (
    SOURCE_DIR,
    plain_dict_id,
    saved_plain_source,
    write_plain_index,
)
from anki_miner.services.dictionary.storage import SCHEMA_VERSION, DictRow, open_readonly, read_meta


def _rows(*pairs: tuple[str, str]) -> list[DictRow]:
    return [DictRow(term=term, reading=None, content=glossary_row([text_nodes(gloss)])) for term, gloss in pairs]


def test_writes_a_managed_slot_with_meta_and_the_kept_source(tmp_path: Path):
    source = tmp_path / "words.csv"
    source.write_text("Casa,house\n", encoding="utf-8")
    messages: list[str] = []

    count = write_plain_index(
        _rows(("Casa", "house"), ("perro", "dog")),
        dest_root=tmp_path / "dicts",
        dict_id="words",
        title="words",
        format_name="csv",
        language="es",
        keep={"words.csv": source},
        overwrite=False,
        expected_rows=2,
        progress=lambda _cur, _total, message: messages.append(message),
    )

    slot = tmp_path / "dicts" / "words"
    assert count == 2
    assert read_ownership_marker(slot) == ("dictionary", "words")
    assert (slot / SOURCE_DIR / "words.csv").read_text(encoding="utf-8") == "Casa,house\n"
    meta = read_meta(slot / "index.sqlite")
    assert meta["format"] == "csv"
    assert meta["source_name"] == "words"
    assert meta["entry_count"] == "2"
    assert meta["language"] == "es"
    assert meta["schema_version"] == str(SCHEMA_VERSION)
    assert read_slot_language(slot) == "es"
    assert "Inserted 2 entries" in messages
    conn = open_readonly(slot / "index.sqlite")
    try:
        # es folds terms with casefold, exactly as its query side does.
        assert [row[0] for row in conn.execute("SELECT term FROM entries ORDER BY id")] == ["casa", "perro"]
    finally:
        conn.close()


def test_no_rows_is_an_error_and_leaves_no_slot(tmp_path: Path):
    with pytest.raises(SetupError, match="no entry"):
        write_plain_index(
            [],
            dest_root=tmp_path / "dicts",
            dict_id="empty",
            title="empty",
            format_name="csv",
            language="es",
            keep={},
            overwrite=False,
        )
    assert not (tmp_path / "dicts" / "empty").exists()


def test_cancel_leaves_no_slot(tmp_path: Path):
    with pytest.raises(OperationCancelled):
        write_plain_index(
            _rows(("casa", "house")),
            dest_root=tmp_path / "dicts",
            dict_id="cancelled",
            title="cancelled",
            format_name="csv",
            language="es",
            keep={},
            overwrite=False,
            cancel_check=lambda: True,
        )
    assert not (tmp_path / "dicts" / "cancelled").exists()


def test_existing_slot_needs_overwrite(tmp_path: Path):
    def write(gloss: str, *, overwrite: bool) -> int:
        return write_plain_index(
            _rows(("casa", gloss)),
            dest_root=tmp_path / "dicts",
            dict_id="d",
            title="d",
            format_name="csv",
            language="es",
            keep={},
            overwrite=overwrite,
        )

    write("house", overwrite=False)

    with pytest.raises(SetupError):
        write("home", overwrite=False)
    assert write("home", overwrite=True) == 1


def test_plain_dict_id_slugs_the_title(tmp_path: Path):
    assert plain_dict_id(tmp_path, "Lithuanian-English FreeDict (lt-en)", "lt") == "lithuanian-english-freedict-lt-en"


def test_saved_source_finds_each_kept_form(tmp_path: Path):
    tar_slot = tmp_path / "a" / SOURCE_DIR
    tar_slot.mkdir(parents=True)
    (tar_slot / "stardict.tar").write_bytes(b"x")
    csv_slot = tmp_path / "b" / SOURCE_DIR
    csv_slot.mkdir(parents=True)
    (csv_slot / "my words.tsv").write_text("a\tb\n", encoding="utf-8")
    ifo_slot = tmp_path / "c" / SOURCE_DIR
    ifo_slot.mkdir(parents=True)
    ifo = ifo_slot / "book.ifo"
    ifo.write_text("StarDict's dict ifo file\n", encoding="utf-8")

    assert saved_plain_source(tmp_path / "a") == ("stardict", tar_slot / "stardict.tar")
    assert saved_plain_source(tmp_path / "b") == ("csv", csv_slot / "my words.tsv")
    assert saved_plain_source(tmp_path / "c") == ("stardict", ifo)
    assert saved_plain_source(tmp_path / "missing") is None

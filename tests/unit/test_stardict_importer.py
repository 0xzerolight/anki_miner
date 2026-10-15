"""StarDict books import into an indexed slot (F-17)."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary import zip_safety
from anki_miner.services.dictionary.importers import stardict_importer
from anki_miner.services.dictionary.importers.plain_index import SOURCE_DIR
from anki_miner.services.dictionary.importers.stardict_importer import StarDictImportResult, import_stardict
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.storage import read_meta
from tests.fixtures.dictionary.build_stardict_fixture import build_stardict_files


def _lookup(dest_root: Path, dict_id: str, language: str, word: str) -> str | None:
    provider = IndexedDictProvider(dict_id, dest_root / dict_id / "index.sqlite", keys=get_profile(language).dict_keys)
    provider.load()
    try:
        return provider.lookup(word)
    finally:
        provider.close()


def test_loose_ifo_with_compressed_parts_imports_and_looks_up(tmp_path: Path):
    ifo = build_stardict_files(tmp_path / "book")
    messages: list[str] = []

    result = import_stardict(
        ifo, tmp_path / "dicts", language="es", progress=lambda _c, _t, message: messages.append(message)
    )

    assert result == StarDictImportResult(
        dict_id="test-stardict", source_name="Test StarDict", entry_count=2, skipped_malformed=0
    )
    definition = _lookup(tmp_path / "dicts", "test-stardict", "es", "Casa")
    assert definition is not None and '<b class="gloss-sc-b">house</b>' in definition
    meta = read_meta(tmp_path / "dicts" / "test-stardict" / "index.sqlite")
    assert (meta["format"], meta["source_name"], meta["language"]) == ("stardict", "Test StarDict", "es")
    assert sorted(p.name for p in (tmp_path / "dicts" / "test-stardict" / SOURCE_DIR).iterdir()) == [
        "test-book.dict.dz",
        "test-book.idx.gz",
        "test-book.ifo",
    ]
    assert "Inserted 2 entries" in messages


def test_plain_text_fields_and_64_bit_offsets(tmp_path: Path):
    ifo = build_stardict_files(
        tmp_path / "book",
        entries=[("gato", b"cat <small>\nfeline")],
        sametypesequence="m",
        compressed=False,
        offset_bits=64,
    )

    import_stardict(ifo, tmp_path / "dicts", language="es")

    definition = _lookup(tmp_path / "dicts", "test-stardict", "es", "gato") or ""
    assert "cat &lt;small&gt;<br>feline" in definition


def test_typed_fields_without_a_sametypesequence(tmp_path: Path):
    blob = b"m" + b"plain part\0" + b"h" + b"<i>html part</i>\0" + b"W" + (4).to_bytes(4, "big") + b"RIFF"
    ifo = build_stardict_files(tmp_path / "book", entries=[("gato", blob)], sametypesequence=None)

    import_stardict(ifo, tmp_path / "dicts", language="es")

    definition = _lookup(tmp_path / "dicts", "test-stardict", "es", "gato") or ""
    assert "plain part" in definition and '<i class="gloss-sc-i">html part</i>' in definition
    assert "RIFF" not in definition


def test_synonyms_point_at_their_entry(tmp_path: Path):
    ifo = build_stardict_files(tmp_path / "book", synonyms=[("casas", 0)])

    result = import_stardict(ifo, tmp_path / "dicts", language="es")

    assert result.entry_count == 3
    assert "house" in (_lookup(tmp_path / "dicts", "test-stardict", "es", "casas") or "")


def test_entries_with_no_text_or_bad_offsets_are_skipped(tmp_path: Path):
    ifo = build_stardict_files(
        tmp_path / "book", entries=[("casa", b"house"), ("vacio", b"<br>  "), ("roto", b"broken")]
    )
    idx = gzip.decompress(ifo.with_name("test-book.idx.gz").read_bytes())
    # Point the last entry past the end of the .dict data.
    idx = idx[:-8] + (10_000).to_bytes(4, "big") + (5).to_bytes(4, "big")
    ifo.with_name("test-book.idx.gz").write_bytes(gzip.compress(idx))

    result = import_stardict(ifo, tmp_path / "dicts", language="es")

    assert result.entry_count == 1
    assert result.skipped_malformed == 2


def test_a_non_japanese_import_folds_keys_for_its_language(tmp_path: Path):
    """lt folds stress marks and case on both sides; the reading column stays empty."""
    ifo = build_stardict_files(tmp_path / "book", entries=[("Namas", b"house")])

    import_stardict(ifo, tmp_path / "dicts", language="lt")

    assert "house" in (_lookup(tmp_path / "dicts", "test-stardict", "lt", "namas") or "")


def test_a_missing_dict_file_is_refused(tmp_path: Path):
    ifo = build_stardict_files(tmp_path / "book")
    ifo.with_name("test-book.dict.dz").unlink()

    with pytest.raises(SetupError, match=".dict"):
        import_stardict(ifo, tmp_path / "dicts", language="es")


def test_not_an_ifo_file_is_refused(tmp_path: Path):
    ifo = tmp_path / "book.ifo"
    ifo.write_text("hello\n", encoding="utf-8")

    with pytest.raises(SetupError, match="not a StarDict"):
        import_stardict(ifo, tmp_path / "dicts", language="es")


def test_a_gzip_bomb_inside_the_book_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(stardict_importer, "MAX_UNCOMPRESSED_BYTES", 10_000)
    ifo = build_stardict_files(tmp_path / "book", entries=[("casa", b"\0" * 50_000)])

    with pytest.raises(SetupError, match="exceeds"):
        import_stardict(ifo, tmp_path / "dicts", language="es")


def test_cancel_leaves_no_slot(tmp_path: Path):
    ifo = build_stardict_files(tmp_path / "book")

    with pytest.raises(OperationCancelled):
        import_stardict(ifo, tmp_path / "dicts", language="es", cancel_check=lambda: True)
    assert not (tmp_path / "dicts" / "test-stardict").exists()


def test_the_budget_is_the_shared_zip_cap():
    assert stardict_importer.MAX_UNCOMPRESSED_BYTES == zip_safety.MAX_UNCOMPRESSED_BYTES

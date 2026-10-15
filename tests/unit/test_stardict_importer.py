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


# --- Archives: FreeDict tarballs and zips (L8.F17.T5) ---------------------------

import io  # noqa: E402
import tarfile  # noqa: E402
import zipfile  # noqa: E402

from anki_miner.services.dictionary.importers.plain_index import saved_plain_source  # noqa: E402
from anki_miner.services.dictionary.importers.stardict_importer import is_stardict_source  # noqa: E402
from tests.fixtures.dictionary.build_stardict_fixture import build_stardict_tar, build_stardict_zip  # noqa: E402
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip  # noqa: E402


@pytest.mark.parametrize("mode", ["w:xz", "w:gz", "w:bz2"])
def test_freedict_style_tarball_imports(tmp_path: Path, mode: str):
    archive = build_stardict_tar(tmp_path / "freedict.tar", mode=mode, compressed=mode != "w:gz")

    result = import_stardict(archive, tmp_path / "dicts", language="es", dict_id="freedict-spa-eng")

    assert result.dict_id == "freedict-spa-eng"
    assert result.entry_count == 2
    assert saved_plain_source(tmp_path / "dicts" / "freedict-spa-eng") == (
        "stardict",
        tmp_path / "dicts" / "freedict-spa-eng" / SOURCE_DIR / "stardict.tar",
    )
    assert "dog" in (_lookup(tmp_path / "dicts", "freedict-spa-eng", "es", "perro") or "")


def test_zip_holding_a_book_imports(tmp_path: Path):
    archive = build_stardict_zip(tmp_path / "book.zip")

    result = import_stardict(archive, tmp_path / "dicts", language="es")

    assert result.entry_count == 2
    assert (tmp_path / "dicts" / result.dict_id / SOURCE_DIR / "stardict.zip").is_file()


def test_two_books_in_one_archive_are_refused(tmp_path: Path):
    archive = tmp_path / "two.tar.gz"
    one = build_stardict_files(tmp_path / "one", name="one")
    two = build_stardict_files(tmp_path / "two", name="two")
    with tarfile.open(archive, "w:gz") as tf:
        for path in [*one.parent.iterdir(), *two.parent.iterdir()]:
            tf.add(path, arcname=path.name)

    with pytest.raises(SetupError, match="2 StarDict dictionaries"):
        import_stardict(archive, tmp_path / "dicts", language="es")


def test_tar_members_are_never_extracted_under_their_own_names(tmp_path: Path):
    archive = tmp_path / "evil.tar"
    book = build_stardict_files(tmp_path / "book")
    with tarfile.open(archive, "w") as tf:
        for path in book.parent.iterdir():
            tf.add(path, arcname=f"../../{path.name}")
        payload = b"owned"
        info = tarfile.TarInfo("../../escaped.txt")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    result = import_stardict(archive, tmp_path / "dicts" / "inner", language="es")

    assert result.entry_count == 2
    assert not (tmp_path / "escaped.txt").exists()


def test_a_tar_declaring_more_than_the_budget_is_refused_before_inflating(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(stardict_importer, "MAX_UNCOMPRESSED_BYTES", 1000)
    archive = build_stardict_tar(tmp_path / "big.tar.xz", entries=[("casa", b"x" * 5000)], compressed=False)

    with pytest.raises(SetupError, match="exceeds"):
        import_stardict(archive, tmp_path / "dicts", language="es")
    assert not (tmp_path / "dicts").exists() or not any((tmp_path / "dicts").iterdir())


def test_a_truncated_tarball_is_a_setup_error(tmp_path: Path):
    archive = build_stardict_tar(tmp_path / "cut.tar.xz", entries=[("casa", b"house " * 3000)])
    data = archive.read_bytes()
    archive.write_bytes(data[: len(data) // 2])

    with pytest.raises(SetupError, match="Corrupt StarDict archive"):
        import_stardict(archive, tmp_path / "dicts", language="es")


def test_zip_path_traversal_is_refused(tmp_path: Path):
    archive = tmp_path / "evil.zip"
    book = build_stardict_files(tmp_path / "book")
    with zipfile.ZipFile(archive, "w") as zf:
        for path in book.parent.iterdir():
            zf.write(path, arcname=f"../{path.name}")

    with pytest.raises(SetupError, match="unsafe path"):
        import_stardict(archive, tmp_path / "dicts", language="es")


def test_is_stardict_source(tmp_path: Path):
    assert is_stardict_source(build_stardict_files(tmp_path / "loose"))
    assert is_stardict_source(build_stardict_tar(tmp_path / "a.tar.bz2", mode="w:bz2"))
    assert is_stardict_source(build_stardict_zip(tmp_path / "b.zip"))
    assert not is_stardict_source(build_yomitan_zip(tmp_path / "y.zip"))
    text = tmp_path / "words.csv"
    text.write_text("casa,house\n", encoding="utf-8")
    assert not is_stardict_source(text)


# --- Repair from the kept source (L8.F17.T6) ------------------------------------

from anki_miner.services.dictionary.importers.stardict_importer import repair_stardict  # noqa: E402


def test_repair_rebuilds_from_the_kept_tarball_keeping_the_language(tmp_path: Path):
    archive = build_stardict_tar(tmp_path / "freedict.tar.xz")
    dest_root = tmp_path / "dicts"
    import_stardict(archive, dest_root, language="lt", dict_id="freedict-lit-eng")
    archive.unlink()
    saved = saved_plain_source(dest_root / "freedict-lit-eng")
    assert saved is not None and saved[0] == "stardict"

    result = repair_stardict(saved[1], dest_root, dict_id="freedict-lit-eng")

    assert result.entry_count == 2
    assert read_meta(dest_root / "freedict-lit-eng" / "index.sqlite")["language"] == "lt"

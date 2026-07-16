"""Tests for the reading-tab input detector (classification + load dispatch)."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.services.reading import detector

_LOADER_MODULES = {
    "mokuro": "anki_miner.services.reading.mokuro_source",
    "epub": "anki_miner.services.reading.epub_source",
    "txt": "anki_miner.services.reading.aozora_source",
    "subtitle": "anki_miner.services.reading.subtitle_source",
    "text": "anki_miner.services.reading.text_source",
}


def _write_mokuro(
    path: Path,
    *,
    title: str = "MyManga",
    volume: str = "Vol1",
    extra: dict | None = None,
) -> None:
    """Write a schema-valid ``.mokuro`` sidecar."""
    data = {
        "version": "0.1.0",
        "title": title,
        "title_uuid": "t-uuid",
        "volume": volume,
        "volume_uuid": "v-uuid",
        "pages": [],
    }
    if extra:
        data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Case 1: a dropped ``.mokuro`` file → single manga volume.
# --------------------------------------------------------------------------- #


def test_mokuro_file_image_root_is_sibling_dir(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok, title="MyManga", volume="Vol1")
    (tmp_path / "Vol1").mkdir()

    refs = detector.detect(mok)

    assert len(refs) == 1
    ref = refs[0]
    assert ref.kind == "mokuro"
    assert ref.path == mok
    assert ref.image_root == tmp_path / "Vol1"
    assert ref.title == "MyManga"
    assert ref.volume == "Vol1"


def test_mokuro_file_image_root_is_cbz_archive(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok)
    cbz = tmp_path / "Vol1.cbz"
    cbz.write_bytes(b"PK\x03\x04")

    refs = detector.detect(mok)

    assert refs[0].image_root == cbz


def test_mokuro_file_archive_extension_is_case_insensitive(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok)
    cbz = tmp_path / "Vol1.CBZ"
    cbz.write_bytes(b"PK")

    refs = detector.detect(mok)

    assert refs[0].image_root == cbz


def test_mokuro_file_zip_archive(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok)
    archive = tmp_path / "Vol1.zip"
    archive.write_bytes(b"PK")

    refs = detector.detect(mok)

    assert refs[0].image_root == archive


def test_mokuro_file_dir_beats_archive(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok)
    (tmp_path / "Vol1").mkdir()
    (tmp_path / "Vol1.cbz").write_bytes(b"PK")

    refs = detector.detect(mok)

    assert refs[0].image_root == tmp_path / "Vol1"


def test_mokuro_file_cbz_beats_zip(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok)
    cbz = tmp_path / "Vol1.cbz"
    cbz.write_bytes(b"PK")
    (tmp_path / "Vol1.zip").write_bytes(b"PK")

    refs = detector.detect(mok)

    assert refs[0].image_root == cbz


def test_mokuro_file_text_only_when_no_images(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok)

    refs = detector.detect(mok)

    assert refs[0].image_root is None
    assert refs[0].kind == "mokuro"


# --------------------------------------------------------------------------- #
# Case 2: a dropped ``.cbz``/``.zip`` → requires a sibling ``.mokuro``.
# --------------------------------------------------------------------------- #


def test_cbz_with_sibling_mokuro(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok, volume="Vol1")
    cbz = tmp_path / "Vol1.cbz"
    cbz.write_bytes(b"PK")

    refs = detector.detect(cbz)

    assert len(refs) == 1
    ref = refs[0]
    assert ref.kind == "mokuro"
    assert ref.path == mok
    assert ref.image_root == cbz
    assert ref.volume == "Vol1"


def test_zip_with_sibling_mokuro(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok)
    archive = tmp_path / "Vol1.zip"
    archive.write_bytes(b"PK")

    refs = detector.detect(archive)

    assert refs[0].path == mok
    assert refs[0].image_root == archive


def test_cbz_missing_sibling_errors_naming_expected(tmp_path):
    cbz = tmp_path / "Vol1.cbz"
    cbz.write_bytes(b"PK")

    with pytest.raises(SetupError) as excinfo:
        detector.detect(cbz)

    assert "Vol1.mokuro" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Case 3: a dropped directory → title dir, dropped image dir, or not mokuro.
# --------------------------------------------------------------------------- #


def test_title_dir_yields_volumes_natural_sorted(tmp_path):
    title_dir = tmp_path / "MyManga"
    title_dir.mkdir()
    for vol in ["Vol1", "Vol2", "Vol10"]:
        _write_mokuro(title_dir / f"{vol}.mokuro", volume=vol)

    refs = detector.detect(title_dir)

    assert [r.volume for r in refs] == ["Vol1", "Vol2", "Vol10"]
    assert all(r.kind == "mokuro" for r in refs)
    assert all(r.image_root is None for r in refs)  # text-only, no image roots here


def test_title_dir_ignores_junk_entries(tmp_path):
    title_dir = tmp_path / "MyManga"
    title_dir.mkdir()
    _write_mokuro(title_dir / "Vol1.mokuro", volume="Vol1")
    _write_mokuro(title_dir / "Vol2.mokuro", volume="Vol2")
    (title_dir / ".DS_Store").write_text("junk")
    (title_dir / "Thumbs.db").write_text("junk")
    (title_dir / "__MACOSX").mkdir()

    refs = detector.detect(title_dir)

    assert [r.volume for r in refs] == ["Vol1", "Vol2"]


def test_dropped_image_dir_finds_sibling_sidecar(tmp_path):
    img_dir = tmp_path / "Vol1"
    img_dir.mkdir()
    (img_dir / "001.jpg").write_bytes(b"img")
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok, volume="Vol1")

    refs = detector.detect(img_dir)

    assert len(refs) == 1
    assert refs[0].path == mok
    # the dropped dir is exactly the sidecar's resolved image root
    assert refs[0].image_root == img_dir


def test_dropped_image_dir_with_dotted_name(tmp_path):
    # A dir named "Vol1.2" pairs with "Vol1.2.mokuro" (name + suffix, not stem).
    img_dir = tmp_path / "Vol1.2"
    img_dir.mkdir()
    mok = tmp_path / "Vol1.2.mokuro"
    _write_mokuro(mok, volume="Vol1.2")

    refs = detector.detect(img_dir)

    assert refs[0].path == mok


def test_directory_not_mokuro_errors(tmp_path):
    plain = tmp_path / "random"
    plain.mkdir()
    (plain / "notes.txt").write_text("hello")

    with pytest.raises(SetupError):
        detector.detect(plain)


# --------------------------------------------------------------------------- #
# Case 4: books — classify by extension, no file open, provisional fill.
# --------------------------------------------------------------------------- #


def test_epub_ref_provisional_fill(tmp_path):
    epub = tmp_path / "My Novel.epub"
    epub.write_bytes(b"PK")

    refs = detector.detect(epub)

    assert len(refs) == 1
    ref = refs[0]
    assert ref.kind == "epub"
    assert ref.path == epub
    assert ref.title == "My Novel"
    assert ref.volume is None
    assert ref.image_root is None


def test_txt_ref_provisional_fill(tmp_path):
    txt = tmp_path / "aozora.txt"
    txt.write_text("本文", encoding="utf-8")

    refs = detector.detect(txt)

    ref = refs[0]
    assert ref.kind == "txt"
    assert ref.path == txt
    assert ref.title == "aozora"
    assert ref.volume is None
    assert ref.image_root is None


def test_epub_ref_does_not_open_the_file(tmp_path):
    # Extension classification must not read bytes: a nonexistent epub still refs.
    epub = tmp_path / "ghost.epub"
    refs = detector.detect(epub)
    assert refs[0].kind == "epub"
    assert refs[0].title == "ghost"


def test_unknown_extension_errors(tmp_path):
    movie = tmp_path / "movie.mp4"
    movie.write_bytes(b"x")

    with pytest.raises(SetupError) as excinfo:
        detector.detect(movie)

    # The guidance message names every supported input class.
    assert ".srt/.ass/.ssa/.vtt" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Case 5: subtitle files — classify by extension, no file open, provisional fill.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ext", [".srt", ".ass", ".ssa", ".vtt"])
def test_subtitle_ref_provisional_fill(tmp_path, ext):
    sub = tmp_path / f"Ep01{ext}"
    sub.write_text("stub", encoding="utf-8")

    refs = detector.detect(sub)

    assert len(refs) == 1
    ref = refs[0]
    assert ref.kind == "subtitle"
    assert ref.path == sub
    assert ref.title == "Ep01"
    assert ref.volume is None
    assert ref.image_root is None


def test_subtitle_ref_uppercase_extension(tmp_path):
    # detect() lowercases the suffix; the ref keeps the original-case path.
    sub = tmp_path / "EP01.SRT"
    refs = detector.detect(sub)
    assert refs[0].kind == "subtitle"
    assert refs[0].path == sub


def test_subtitle_ref_does_not_open_the_file(tmp_path):
    refs = detector.detect(tmp_path / "ghost.srt")
    assert refs[0].kind == "subtitle"
    assert refs[0].title == "ghost"


def test_microdvd_sub_not_recognized(tmp_path):
    # MicroDVD is frame-based (needs fps we don't have) — deliberately cut.
    with pytest.raises(SetupError):
        detector.detect(tmp_path / "movie.sub")


# --------------------------------------------------------------------------- #
# ``.mokuro`` schema validation.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "missing_key",
    ["version", "title", "title_uuid", "volume", "volume_uuid", "pages"],
)
def test_mokuro_missing_required_key_errors(tmp_path, missing_key):
    data = {
        "version": "1",
        "title": "T",
        "title_uuid": "a",
        "volume": "V",
        "volume_uuid": "b",
        "pages": [],
    }
    del data[missing_key]
    mok = tmp_path / "Vol1.mokuro"
    mok.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SetupError) as excinfo:
        detector.detect(mok)

    assert missing_key in str(excinfo.value)


def test_mokuro_unknown_keys_accepted(tmp_path):
    # Community files carry extra keys (chars/spine_width) — ignore them.
    mok = tmp_path / "Vol1.mokuro"
    _write_mokuro(mok, extra={"chars": 12345, "spine_width": 3.2})

    refs = detector.detect(mok)

    assert refs[0].title == "MyManga"
    assert refs[0].volume == "Vol1"


def test_mokuro_invalid_json_errors(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    mok.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(SetupError):
        detector.detect(mok)


def test_mokuro_non_dict_json_errors(tmp_path):
    mok = tmp_path / "Vol1.mokuro"
    mok.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(SetupError):
        detector.detect(mok)


def test_mokuro_missing_file_errors(tmp_path):
    with pytest.raises(SetupError):
        detector.detect(tmp_path / "ghost.mokuro")


# --------------------------------------------------------------------------- #
# ``load()`` dispatcher — lazy per-kind import to the source loaders.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ["mokuro", "epub", "txt", "subtitle", "text"])
def test_load_dispatches_to_source_module(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # ``detector.load`` does ``from . import <loader>`` then ``<loader>.load(ref)``.
    # The loaders really exist post-integration, so patch the real seam: monkeypatch
    # the imported module's ``load`` attribute (faking sys.modules can't win once the
    # real module is imported — ``from . import`` resolves through the package attr).
    # kind="text" refs are pathless by contract, so build them that way here to
    # prove the dispatch path works without a path.
    if kind == "text":
        ref = ReadingSourceRef(kind="text", title="Text", text="x")
    else:
        ref = ReadingSourceRef(
            kind=kind,  # type: ignore[arg-type]
            path=Path("whatever"),
            image_root=None,
            title="T",
            volume=None,
        )
    module = importlib.import_module(_LOADER_MODULES[kind])
    sentinel = object()
    fake_load = MagicMock(return_value=sentinel)
    monkeypatch.setattr(module, "load", fake_load)

    result = detector.load(ref)

    assert result is sentinel
    fake_load.assert_called_once_with(ref)


def test_load_does_not_import_sibling_modules():
    # Dispatching a mokuro ref must not import the epub/txt loaders. Run in a fresh
    # interpreter for total isolation: sibling loaders imported by other tests in the
    # same process would otherwise be present in sys.modules and mask a regression.
    script = textwrap.dedent("""
        import sys
        import types
        from pathlib import Path

        from anki_miner.services.reading import detector
        from anki_miner.models.reading import ReadingSourceRef

        # Stub the mokuro loader (both sys.modules and the package attr, so
        # ``from . import mokuro_source`` resolves to the stub) to avoid disk I/O.
        stub = types.ModuleType("anki_miner.services.reading.mokuro_source")
        stub.load = lambda ref: None
        sys.modules["anki_miner.services.reading.mokuro_source"] = stub
        import anki_miner.services.reading as pkg

        pkg.mokuro_source = stub

        ref = ReadingSourceRef(
            kind="mokuro",
            path=Path("x.mokuro"),
            image_root=None,
            title="T",
            volume="1",
        )
        detector.load(ref)

        siblings = [
            "anki_miner.services.reading.epub_source",
            "anki_miner.services.reading.aozora_source",
        ]
        leaked = [name for name in siblings if name in sys.modules]
        assert not leaked, leaked
        """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_load_unknown_kind_errors():
    ref = ReadingSourceRef(
        kind="bogus",  # type: ignore[arg-type]
        path=Path("x"),
        image_root=None,
        title="T",
        volume=None,
    )
    with pytest.raises(SetupError):
        detector.load(ref)


# --------------------------------------------------------------------------- #
# detect_book_folder: top-level .epub/.txt enumeration (Novels folder mining).
# --------------------------------------------------------------------------- #


def test_book_folder_yields_books_natural_sorted(tmp_path):
    (tmp_path / "Vol10.epub").write_bytes(b"PK")
    (tmp_path / "Vol2.epub").write_bytes(b"PK")
    (tmp_path / "Vol1.epub").write_bytes(b"PK")

    refs = detector.detect_book_folder(tmp_path)

    assert [r.title for r in refs] == ["Vol1", "Vol2", "Vol10"]
    assert all(r.kind == "epub" for r in refs)


def test_book_folder_mixes_epub_and_txt(tmp_path):
    (tmp_path / "b.txt").write_text("本文", encoding="utf-8")
    (tmp_path / "a.epub").write_bytes(b"PK")

    refs = detector.detect_book_folder(tmp_path)

    assert [(r.title, r.kind) for r in refs] == [("a", "epub"), ("b", "txt")]


def test_book_folder_extension_case_insensitive(tmp_path):
    (tmp_path / "loud.EPUB").write_bytes(b"PK")
    (tmp_path / "shout.TXT").write_text("x", encoding="utf-8")

    refs = detector.detect_book_folder(tmp_path)

    assert [(r.title, r.kind) for r in refs] == [("loud", "epub"), ("shout", "txt")]


def test_book_folder_refs_are_provisional_no_file_open(tmp_path):
    # Extension-only classification: title is the stem, loader stays authoritative.
    (tmp_path / "My Novel.epub").write_bytes(b"PK")

    ref = detector.detect_book_folder(tmp_path)[0]

    assert ref.path == tmp_path / "My Novel.epub"
    assert ref.title == "My Novel"
    assert ref.volume is None
    assert ref.image_root is None


def test_book_folder_is_not_recursive(tmp_path):
    (tmp_path / "top.txt").write_text("x", encoding="utf-8")
    nested = tmp_path / "series"
    nested.mkdir()
    (nested / "nested.epub").write_bytes(b"PK")

    refs = detector.detect_book_folder(tmp_path)

    assert [r.title for r in refs] == ["top"]


def test_book_folder_ignores_non_books_and_junk(tmp_path):
    (tmp_path / "keep.epub").write_bytes(b"PK")
    (tmp_path / "cover.jpg").write_bytes(b"x")
    (tmp_path / "book.mokuro").write_text("{}", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"x")
    fake_dir = tmp_path / "dir.txt"  # a directory with a book suffix is not a book
    fake_dir.mkdir()

    refs = detector.detect_book_folder(tmp_path)

    assert [r.title for r in refs] == ["keep"]


def test_book_folder_empty_errors_with_name_and_manga_hint(tmp_path):
    empty = tmp_path / "Comics"
    empty.mkdir()

    with pytest.raises(SetupError) as excinfo:
        detector.detect_book_folder(empty)

    msg = str(excinfo.value)
    assert "Comics" in msg
    assert "Manga" in msg


def test_book_folder_mokuro_only_errors_with_manga_hint(tmp_path):
    (tmp_path / "vol1.mokuro").write_text("{}", encoding="utf-8")

    with pytest.raises(SetupError) as excinfo:
        detector.detect_book_folder(tmp_path)

    assert "Manga" in str(excinfo.value)


def test_book_folder_unreadable_errors(tmp_path):
    with pytest.raises(SetupError):
        detector.detect_book_folder(tmp_path / "missing")

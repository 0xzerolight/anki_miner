"""End-to-end wiring tests: real ``detect()`` → real ``load()``, no mocks.

Every other reading-source suite either drives one loader in isolation (with a
hand-built ``ReadingSourceRef``) or exercises ``detector.load`` against a mocked
per-kind ``load`` seam. This file closes the gap: it builds real fixtures in
``tmp_path``, runs the actual detector, then feeds each detected ref through the
actual lazy-import dispatcher to a populated ``ReadingDocument`` — for all three
kinds, with zero mocks anywhere. It proves the *wiring*, not per-format edge
cases (the unit suites own those).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from anki_miner.models.reading import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
)
from anki_miner.services.reading import detector

# Tiny real image blobs — pairing/cover peek reads magic bytes only, never decodes.
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _is_japanese(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF  # hiragana + katakana
        or 0x3400 <= o <= 0x9FFF  # CJK ideographs (+ ext A)
        or 0xF900 <= o <= 0xFAFF  # CJK compatibility ideographs
    )


def _has_japanese(text: str) -> bool:
    return any(_is_japanese(c) for c in text)


# --------------------------------------------------------------------------- #
# Mokuro fixture builders
# --------------------------------------------------------------------------- #


def _mokuro_json(pages: list[dict], *, title: str, volume: str) -> str:
    return json.dumps(
        {
            "version": "0.2.4",
            "title": title,
            "title_uuid": "t-uuid",
            "volume": volume,
            "volume_uuid": "v-uuid",
            "pages": pages,
        },
        ensure_ascii=False,
    )


def _page(img_path: str, lines: list[str]) -> dict:
    return {
        "version": "0.2.4",
        "img_width": 800,
        "img_height": 1200,
        "img_path": img_path,
        "blocks": [
            {
                "box": [0, 0, 100, 200],
                "vertical": True,
                "font_size": 24,
                "lines": lines,
                "lines_coords": [[[0, 0], [1, 1]]],
            }
        ],
    }


def _write_mokuro_volume(directory: Path, stem: str, *, title: str, volume: str) -> Path:
    """Write ``<stem>.mokuro`` + a sibling ``<stem>/`` image dir (2 pages)."""
    pages = [
        _page("001.jpg", ["これは", "テスト"]),
        _page("002.jpg", ["にほんご"]),
    ]
    mokuro_path = directory / f"{stem}.mokuro"
    mokuro_path.write_text(_mokuro_json(pages, title=title, volume=volume), encoding="utf-8")
    img_dir = directory / stem
    img_dir.mkdir()
    (img_dir / "001.jpg").write_bytes(_JPEG_BYTES)
    (img_dir / "002.jpg").write_bytes(_JPEG_BYTES)
    return mokuro_path


# --------------------------------------------------------------------------- #
# EPUB fixture builder
# --------------------------------------------------------------------------- #

_CONTAINER = (
    '<?xml version="1.0"?>\n'
    '<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    "  <rootfiles>\n"
    '    <rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/>\n'
    "  </rootfiles>\n"
    "</container>\n"
)


def _opf(title: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="pub-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f"    <dc:title>{title}</dc:title>\n"
        "    <dc:creator>著者名</dc:creator>\n"
        "  </metadata>\n"
        "  <manifest>\n"
        '    <item id="cov" href="cover.png" media-type="image/png" properties="cover-image"/>\n'
        '    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n'
        '    <item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>\n'
        "  </manifest>\n"
        '  <spine>\n    <itemref idref="c1"/>\n    <itemref idref="c2"/>\n  </spine>\n'
        "</package>\n"
    )


def _xhtml(paragraph: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        "<head><title>x</title></head>\n"
        f"<body>\n<p>{paragraph}</p>\n</body>\n</html>\n"
    )


def _write_epub(path: Path, *, title: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", _opf(title))
        zf.writestr("OEBPS/cover.png", _PNG_BYTES)
        zf.writestr("OEBPS/ch1.xhtml", _xhtml("吾輩は猫である。名前はまだ無い。"))
        zf.writestr("OEBPS/ch2.xhtml", _xhtml("どこで生れたか。"))


# --------------------------------------------------------------------------- #
# Aozora fixture
# --------------------------------------------------------------------------- #

_AOZORA = "\n".join(
    [
        "雪国",
        "川端康成",
        "",
        "　国境《くにざかい》の長いトンネルを抜けると雪国であった。",
        "　彼は※［＃「木＋温のつくり」、第3水準1-85-73］の木を見た。",
        "",
        "底本：「日本文学全集」筑摩書房",
        "青空文庫作成ファイル：",
    ]
)


# --------------------------------------------------------------------------- #
# 1. Mokuro: detect a dropped .mokuro → load one manga volume with images.
# --------------------------------------------------------------------------- #


def test_mokuro_detect_then_load_populates_manga_document(tmp_path: Path) -> None:
    mokuro_path = _write_mokuro_volume(tmp_path, "Vol1", title="MyManga", volume="Vol1")

    refs = detector.detect(mokuro_path)

    assert len(refs) == 1
    ref = refs[0]
    assert isinstance(ref, ReadingSourceRef)
    assert ref.kind == "mokuro"
    assert ref.title == "MyManga"
    assert ref.volume == "Vol1"
    assert ref.image_root == tmp_path / "Vol1"

    doc = detector.load(ref)

    assert isinstance(doc, ReadingDocument)
    assert doc.kind == "manga"
    assert doc.series == "MyManga"
    assert doc.episode == "Vol1"
    assert [u.text for u in doc.units] == ["これはテスト", "にほんご"]
    assert [u.location_label for u in doc.units] == ["p.1", "p.2"]
    assert doc.units[0].image_ref == ImageRef(tmp_path / "Vol1" / "001.jpg")
    assert doc.units[1].image_ref == ImageRef(tmp_path / "Vol1" / "002.jpg")


# --------------------------------------------------------------------------- #
# 2. EPUB: detect a dropped .epub → load a book with a shared cover ref.
# --------------------------------------------------------------------------- #


def test_epub_detect_then_load_populates_book_document(tmp_path: Path) -> None:
    epub_path = tmp_path / "MyNovel.epub"
    _write_epub(epub_path, title="作品タイトル")

    refs = detector.detect(epub_path)

    assert len(refs) == 1
    ref = refs[0]
    assert ref.kind == "epub"
    assert ref.title == "MyNovel"  # provisional file-stem label
    assert ref.image_root is None

    doc = detector.load(ref)

    assert doc.kind == "book"
    assert doc.series == "Books"
    assert doc.episode == "作品タイトル"  # dc:title, loader-authoritative
    assert doc.title == "作品タイトル"
    assert [u.text for u in doc.units] == ["吾輩は猫である。", "名前はまだ無い。", "どこで生れたか。"]
    expected_cover = ImageRef(epub_path, "OEBPS/cover.png")
    assert all(u.image_ref == expected_cover for u in doc.units)


# --------------------------------------------------------------------------- #
# 3. Aozora .txt: detect → load, ruby stripped, gaiji resolved, header title.
# --------------------------------------------------------------------------- #


def test_aozora_detect_then_load_populates_book_document(tmp_path: Path) -> None:
    txt_path = tmp_path / "novel.txt"
    txt_path.write_bytes(_AOZORA.encode("cp932"))

    refs = detector.detect(txt_path)

    assert len(refs) == 1
    ref = refs[0]
    assert ref.kind == "txt"
    assert ref.title == "novel"  # provisional file-stem label

    doc = detector.load(ref)

    assert doc.kind == "book"
    assert doc.series == "Books"
    assert doc.episode == "雪国"  # Aozora header title, loader-authoritative
    assert doc.title == "雪国"
    assert [u.text for u in doc.units] == [
        "国境の長いトンネルを抜けると雪国であった。",  # ruby 《くにざかい》 stripped
        "彼は棈の木を見た。",  # gaiji resolved
    ]
    assert all(u.image_ref is None for u in doc.units)
    joined = "".join(u.text for u in doc.units)
    assert "くにざかい" not in joined  # ruby reading gone
    assert "底本" not in joined  # colophon cut


# --------------------------------------------------------------------------- #
# 4. Title directory: detect two volumes natural-sorted → load each.
# --------------------------------------------------------------------------- #


def test_title_dir_detect_two_volumes_then_load_each(tmp_path: Path) -> None:
    title_dir = tmp_path / "MyManga"
    title_dir.mkdir()
    _write_mokuro_volume(title_dir, "Vol2", title="MyManga", volume="Vol2")
    _write_mokuro_volume(title_dir, "Vol10", title="MyManga", volume="Vol10")

    refs = detector.detect(title_dir)

    # Natural sort places Vol2 before Vol10 (not lexicographic Vol10 < Vol2).
    assert [r.volume for r in refs] == ["Vol2", "Vol10"]
    assert all(r.kind == "mokuro" for r in refs)

    for ref in refs:
        doc = detector.load(ref)
        assert doc.kind == "manga"
        assert doc.series == "MyManga"
        assert doc.episode == ref.volume
        assert [u.location_label for u in doc.units] == ["p.1", "p.2"]
        assert doc.units[0].image_ref == ImageRef(title_dir / ref.volume / "001.jpg")


# --------------------------------------------------------------------------- #
# 5. Cross-kind sanity: every document loads non-empty units of real Japanese.
# --------------------------------------------------------------------------- #


def test_every_kind_yields_nonempty_japanese_units(tmp_path: Path) -> None:
    mokuro_path = _write_mokuro_volume(tmp_path, "Vol1", title="漫画", volume="1")
    epub_path = tmp_path / "book.epub"
    _write_epub(epub_path, title="小説")
    txt_path = tmp_path / "aozora.txt"
    txt_path.write_bytes(_AOZORA.encode("cp932"))

    for source in (mokuro_path, epub_path, txt_path):
        for ref in detector.detect(source):
            doc = detector.load(ref)
            assert doc.units, f"no units for {source.name}"
            assert all(u.text for u in doc.units)
            assert all(_has_japanese(u.text) for u in doc.units)

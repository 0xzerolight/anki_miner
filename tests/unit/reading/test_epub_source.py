"""Tests for the EPUB → ReadingDocument loader.

Every fixture builds a mini ``.epub`` in ``tmp_path`` with ``zipfile`` (a
container + OPF + XHTML strings), so the loader is exercised end-to-end against
real zip bytes with no on-disk sample files.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.models.reading import ImageRef, ReadingSourceRef
from anki_miner.services.reading.epub_source import load

# --------------------------------------------------------------------------- #
# Fixture builders
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

# 8-byte PNG signature + filler — passes the magic-byte peek, never decoded.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _build_epub(tmp_path: Path, files: dict[str, bytes | str], name: str = "book.epub") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        for arc, data in files.items():
            zf.writestr(arc, data.encode("utf-8") if isinstance(data, str) else data)
    return path


def _ref(path: Path) -> ReadingSourceRef:
    return ReadingSourceRef(kind="epub", path=path, image_root=None, title=path.stem, volume=None)


def _opf(
    manifest_items: list[tuple[str, str, str, str]],
    spine: list[tuple[str, str | None]],
    *,
    title: str | None = "作品タイトル",
    metas: str = "",
    spine_toc: str | None = None,
) -> str:
    man = "\n    ".join(
        f'<item id="{i}" href="{h}" media-type="{mt}"' + (f' properties="{pr}"' if pr else "") + "/>"
        for (i, h, mt, pr) in manifest_items
    )
    sp = "\n    ".join(f'<itemref idref="{ir}"' + (f' linear="{ln}"' if ln else "") + "/>" for (ir, ln) in spine)
    toc_attr = f' toc="{spine_toc}"' if spine_toc else ""
    title_el = f"<dc:title>{title}</dc:title>" if title is not None else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="pub-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f"    {title_el}\n"
        "    <dc:creator>著者名</dc:creator>\n"
        f"    {metas}\n"
        "  </metadata>\n"
        f"  <manifest>\n    {man}\n  </manifest>\n"
        f"  <spine{toc_attr}>\n    {sp}\n  </spine>\n"
        "</package>\n"
    )


def _xhtml(body_inner: str, *, epub_type: str | None = None) -> str:
    et = f' epub:type="{epub_type}"' if epub_type else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops">\n'
        "<head><title>x</title></head>\n"
        f"<body{et}>\n{body_inner}\n</body>\n</html>\n"
    )


def _nav(entries: list[tuple[str, str]]) -> str:
    lis = "\n".join(f'<li><a href="{h}">{lbl}</a></li>' for (h, lbl) in entries)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops">\n'
        "<head><title>toc</title></head>\n"
        f'<body>\n<nav epub:type="toc"><ol>{lis}</ol></nav>\n</body>\n</html>\n'
    )


def _ncx(entries: list[tuple[str, str]]) -> str:
    pts = "\n".join(
        f'<navPoint id="np{n}"><navLabel><text>{lbl}</text></navLabel>' f'<content src="{src}"/></navPoint>'
        for n, (src, lbl) in enumerate(entries)
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f"<navMap>{pts}</navMap>\n</ncx>\n"
    )


def _encryption(algorithm: str, uri: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"\n'
        '            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">\n'
        "  <enc:EncryptedData>\n"
        f'    <enc:EncryptionMethod Algorithm="{algorithm}"/>\n'
        "    <enc:CipherData>\n"
        f'      <enc:CipherReference URI="{uri}"/>\n'
        "    </enc:CipherData>\n"
        "  </enc:EncryptedData>\n"
        "</encryption>\n"
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_basic_load_metadata_spine_and_chapters(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [
                ("nav", "nav.xhtml", "application/xhtml+xml", "nav"),
                ("c1", "ch1.xhtml", "application/xhtml+xml", ""),
                ("c2", "ch2.xhtml", "application/xhtml+xml", ""),
            ],
            [("c1", None), ("c2", None)],
        ),
        "OEBPS/nav.xhtml": _nav([("ch1.xhtml", "第一章"), ("ch2.xhtml", "第二章")]),
        "OEBPS/ch1.xhtml": _xhtml("<p>吾輩は猫である。名前はまだ無い。</p>"),
        "OEBPS/ch2.xhtml": _xhtml("<p>どこで生れたか。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert doc.kind == "book"
    assert doc.series == "Books"
    assert doc.title == "作品タイトル"
    assert doc.episode == "作品タイトル"
    assert [u.text for u in doc.units] == ["吾輩は猫である。", "名前はまだ無い。", "どこで生れたか。"]
    assert [u.index for u in doc.units] == [0, 1, 2]
    assert [u.location_label for u in doc.units] == ["第一章", "第一章", "第二章"]
    assert all(u.image_ref is None for u in doc.units)


def test_ruby_rt_rp_stripped_to_base_text(tmp_path: Path) -> None:
    body = (
        "<p>私は<ruby>漢字<rt>かんじ</rt></ruby>が好きだ。</p>"
        "<p><ruby>本<rp>(</rp><rt>ほん</rt><rp>)</rp></ruby>を読む。</p>"
    )
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch1.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
        ),
        "OEBPS/ch1.xhtml": _xhtml(body),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    texts = [u.text for u in doc.units]
    assert texts == ["私は漢字が好きだ。", "本を読む。"]
    joined = "".join(texts)
    assert "かんじ" not in joined
    assert "ほん" not in joined
    assert "(" not in joined and ")" not in joined


def test_cover_epub3_property_on_every_unit(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [
                ("cov", "cover.png", "image/png", "cover-image"),
                ("c1", "ch1.xhtml", "application/xhtml+xml", ""),
            ],
            [("c1", None)],
        ),
        "OEBPS/cover.png": _PNG_BYTES,
        "OEBPS/ch1.xhtml": _xhtml("<p>本文一。本文二。</p>"),
    }
    epub_path = _build_epub(tmp_path, files)
    doc = load(_ref(epub_path))

    expected = ImageRef(epub_path, "OEBPS/cover.png")
    assert len(doc.units) == 2
    assert all(u.image_ref == expected for u in doc.units)
    assert doc.warnings == []


def test_cover_epub2_meta_fallback(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [
                ("cover-img", "img/cover.jpg", "image/jpeg", ""),
                ("c1", "ch1.xhtml", "application/xhtml+xml", ""),
            ],
            [("c1", None)],
            metas='<meta name="cover" content="cover-img"/>',
        ),
        "OEBPS/img/cover.jpg": _JPEG_BYTES,
        "OEBPS/ch1.xhtml": _xhtml("<p>本文。</p>"),
    }
    epub_path = _build_epub(tmp_path, files)
    doc = load(_ref(epub_path))

    assert doc.units[0].image_ref == ImageRef(epub_path, "OEBPS/img/cover.jpg")


def test_corrupt_cover_warns_and_mines_without_picture(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [
                ("cov", "cover.svg", "image/svg+xml", "cover-image"),
                ("c1", "ch1.xhtml", "application/xhtml+xml", ""),
            ],
            [("c1", None)],
        ),
        "OEBPS/cover.svg": "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        "OEBPS/ch1.xhtml": _xhtml("<p>本文。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert doc.units and all(u.image_ref is None for u in doc.units)
    assert any("cover" in w.lower() for w in doc.warnings)


def test_drm_content_encryption_raises(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch1.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
        ),
        "OEBPS/ch1.xhtml": _xhtml("<p>本文。</p>"),
        "META-INF/encryption.xml": _encryption("http://www.w3.org/2001/04/xmlenc#aes256-cbc", "OEBPS/ch1.xhtml"),
    }
    epub_path = _build_epub(tmp_path, files, name="protected.epub")
    with pytest.raises(SetupError) as exc:
        load(_ref(epub_path))
    msg = str(exc.value)
    assert "DRM" in msg
    assert "protected.epub" in msg


def test_font_obfuscation_encryption_proceeds(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch1.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
        ),
        "OEBPS/ch1.xhtml": _xhtml("<p>本文。</p>"),
        "OEBPS/fonts/gothic.otf": b"\x00\x01\x00\x00font",
        "META-INF/encryption.xml": _encryption("http://www.idpf.org/2008/embedding", "OEBPS/fonts/gothic.otf"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))
    assert [u.text for u in doc.units] == ["本文。"]


def test_gaiji_image_contributes_no_text_and_warns(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch1.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
        ),
        "OEBPS/ch1.xhtml": _xhtml('<p>外字<img src="gaiji.png" alt=""/>あり。</p>'),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert [u.text for u in doc.units] == ["外字あり。"]
    assert any("gaiji" in w.lower() for w in doc.warnings)


def test_boilerplate_spine_files_skipped(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [
                ("cov", "p-cover.xhtml", "application/xhtml+xml", ""),
                ("colo", "p-colophon.xhtml", "application/xhtml+xml", ""),
                ("sec", "p-titlepage.xhtml", "application/xhtml+xml", ""),
                ("c1", "p-001.xhtml", "application/xhtml+xml", ""),
            ],
            [("cov", None), ("colo", None), ("sec", None), ("c1", None)],
        ),
        "OEBPS/p-cover.xhtml": _xhtml("<p>表紙テキスト。</p>"),
        "OEBPS/p-colophon.xhtml": _xhtml("<p>奥付テキスト。</p>"),
        "OEBPS/p-titlepage.xhtml": _xhtml("<p>扉テキスト。</p>", epub_type="cover"),
        "OEBPS/p-001.xhtml": _xhtml("<p>本編の一文。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    texts = [u.text for u in doc.units]
    assert texts == ["本編の一文。"]
    joined = "".join(texts)
    assert "表紙" not in joined and "奥付" not in joined and "扉" not in joined


def test_boilerplate_name_matching_uses_tokens_not_substrings(tmp_path: Path) -> None:
    # ``protocol`` contains "toc" and ``discover-chapter`` contains "cover" as raw
    # substrings — token matching must mine them, while true boilerplate is skipped.
    files = {
        "OEBPS/content.opf": _opf(
            [
                ("proto", "protocol.xhtml", "application/xhtml+xml", ""),
                ("disc", "discover-chapter.xhtml", "application/xhtml+xml", ""),
                ("toc", "toc.xhtml", "application/xhtml+xml", ""),
                ("colo", "p-colophon.xhtml", "application/xhtml+xml", ""),
                ("ad", "p-ad-001.xhtml", "application/xhtml+xml", ""),
                ("cov", "cover.xhtml", "application/xhtml+xml", ""),
            ],
            [
                ("proto", None),
                ("disc", None),
                ("toc", None),
                ("colo", None),
                ("ad", None),
                ("cov", None),
            ],
        ),
        "OEBPS/protocol.xhtml": _xhtml("<p>プロトコル本文。</p>"),
        "OEBPS/discover-chapter.xhtml": _xhtml("<p>発見の章。</p>"),
        "OEBPS/toc.xhtml": _xhtml("<p>目次テキスト。</p>"),
        "OEBPS/p-colophon.xhtml": _xhtml("<p>奥付テキスト。</p>"),
        "OEBPS/p-ad-001.xhtml": _xhtml("<p>広告テキスト。</p>"),
        "OEBPS/cover.xhtml": _xhtml("<p>表紙テキスト。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert [u.text for u in doc.units] == ["プロトコル本文。", "発見の章。"]


def test_pretty_printed_paragraph_collapsed_to_single_line(tmp_path: Path) -> None:
    body = "<p>\n  これは\n  テストの文です。\n</p>"
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch1.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
        ),
        "OEBPS/ch1.xhtml": _xhtml(body),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert [u.text for u in doc.units] == ["これはテストの文です。"]


def test_unmatched_opener_paragraph_splits_into_sentences(tmp_path: Path) -> None:
    # Shared-splitter fix: a paragraph whose 「 is never closed used to collapse
    # into one over-long unit (same "wall of text" defect as manga); it now
    # splits on the internal 。.
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch1.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
        ),
        "OEBPS/ch1.xhtml": _xhtml("<p>「あの人は言った。それから去った</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert [u.text for u in doc.units] == ["「あの人は言った。", "それから去った"]


def test_balanced_quote_with_attribution_stays_one_unit(tmp_path: Path) -> None:
    # A matched 「」 still suppresses its internal terminator: quote plus
    # attribution remains a single mining unit (no over-splitting).
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch1.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
        ),
        "OEBPS/ch1.xhtml": _xhtml("<p>「行くぞ。」と彼は言った。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert [u.text for u in doc.units] == ["「行くぞ。」と彼は言った。"]


def test_chapter_fallback_to_spine_index(tmp_path: Path) -> None:
    # Nav lists only boilerplate labels → fewer than two usable → ch.{i}.
    files = {
        "OEBPS/content.opf": _opf(
            [
                ("nav", "nav.xhtml", "application/xhtml+xml", "nav"),
                ("c1", "ch1.xhtml", "application/xhtml+xml", ""),
                ("c2", "ch2.xhtml", "application/xhtml+xml", ""),
            ],
            [("c1", None), ("c2", None)],
        ),
        "OEBPS/nav.xhtml": _nav([("ch1.xhtml", "目次"), ("ch2.xhtml", "表紙")]),
        "OEBPS/ch1.xhtml": _xhtml("<p>一つ目。</p>"),
        "OEBPS/ch2.xhtml": _xhtml("<p>二つ目。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert [u.location_label for u in doc.units] == ["ch.0", "ch.1"]


def test_chapters_from_ncx(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [
                ("ncx", "toc.ncx", "application/x-dtbncx+xml", ""),
                ("c1", "ch1.xhtml", "application/xhtml+xml", ""),
                ("c2", "ch2.xhtml", "application/xhtml+xml", ""),
            ],
            [("c1", None), ("c2", None)],
            spine_toc="ncx",
        ),
        "OEBPS/toc.ncx": _ncx([("ch1.xhtml", "序章"), ("ch2.xhtml", "終章")]),
        "OEBPS/ch1.xhtml": _xhtml("<p>はじめ。</p>"),
        "OEBPS/ch2.xhtml": _xhtml("<p>おわり。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert [u.location_label for u in doc.units] == ["序章", "終章"]


def test_linear_no_spine_item_skipped(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [
                ("c1", "ch1.xhtml", "application/xhtml+xml", ""),
                ("aux", "aux.xhtml", "application/xhtml+xml", ""),
            ],
            [("c1", None), ("aux", "no")],
        ),
        "OEBPS/ch1.xhtml": _xhtml("<p>本編。</p>"),
        "OEBPS/aux.xhtml": _xhtml("<p>補助資料。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert [u.text for u in doc.units] == ["本編。"]


def test_href_percent_encoding_unquoted(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch%20one.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
        ),
        "OEBPS/ch one.xhtml": _xhtml("<p>空白入り。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert [u.text for u in doc.units] == ["空白入り。"]


def test_u3000_leading_stripped_internal_kept(tmp_path: Path) -> None:
    body = "<p>　インデント有り。</p><p>前　後ろ。</p>"
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch1.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
        ),
        "OEBPS/ch1.xhtml": _xhtml(body),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    texts = [u.text for u in doc.units]
    assert texts == ["インデント有り。", "前　後ろ。"]


def test_br_splits_paragraphs(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch1.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
        ),
        "OEBPS/ch1.xhtml": _xhtml("<p>一行目<br/>二行目。</p>"),
    }
    doc = load(_ref(_build_epub(tmp_path, files)))

    assert [u.text for u in doc.units] == ["一行目", "二行目。"]


def test_missing_container_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("OEBPS/content.opf", "<package/>")
    with pytest.raises(SetupError):
        load(_ref(path))


def test_title_falls_back_to_ref(tmp_path: Path) -> None:
    files = {
        "OEBPS/content.opf": _opf(
            [("c1", "ch1.xhtml", "application/xhtml+xml", "")],
            [("c1", None)],
            title=None,
        ),
        "OEBPS/ch1.xhtml": _xhtml("<p>本文。</p>"),
    }
    epub_path = _build_epub(tmp_path, files, name="源氏物語.epub")
    doc = load(_ref(epub_path))

    assert doc.title == "源氏物語"
    assert doc.episode == "源氏物語"

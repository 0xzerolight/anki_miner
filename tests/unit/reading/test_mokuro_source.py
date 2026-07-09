"""Tests for the mokuro volume loader (mokuro_source.load)."""

import json
import unicodedata
import zipfile
from pathlib import Path

from anki_miner.services.reading.models import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
)
from anki_miner.services.reading.mokuro_source import _BLOCK_SPLIT_THRESHOLD, load

# ---------------------------------------------------------------------------
# Fixture builders (dict -> json.dumps -> tmp_path). No real images are opened,
# so 1x1-ish byte blobs are plenty when pairing needs on-disk/archive files.
# ---------------------------------------------------------------------------
_IMG_BYTES = b"\xff\xd8\xff\xd9"  # tiny jpeg-ish blob; pairing never decodes it


def _block(lines, *, box=None, vertical=True, font_size=24, **extra):
    return {
        "box": box or [0, 0, 100, 200],
        "vertical": vertical,
        "font_size": font_size,
        "lines": lines,
        "lines_coords": [[[0, 0], [1, 1]]],
        **extra,
    }


def _page(img_path, blocks, *, version="0.2.4", **extra):
    return {
        "version": version,
        "img_width": 800,
        "img_height": 1200,
        "img_path": img_path,
        "blocks": blocks,
        **extra,
    }


def _mokuro(pages, *, title="JsonSeries", volume="9", version="0.2.4", **extra):
    return {
        "version": version,
        "title": title,
        "title_uuid": "t-uuid",
        "volume": volume,
        "volume_uuid": "v-uuid",
        "pages": pages,
        **extra,
    }


def _write_ref(
    tmp_path: Path,
    mokuro_dict: dict,
    image_root: Path | None,
    *,
    title="RefSeries",
    volume="1",
) -> ReadingSourceRef:
    mpath = tmp_path / "vol.mokuro"
    mpath.write_text(json.dumps(mokuro_dict, ensure_ascii=False), encoding="utf-8")
    return ReadingSourceRef(
        kind="mokuro",
        path=mpath,
        image_root=image_root,
        title=title,
        volume=volume,
    )


# ---------------------------------------------------------------------------
# Metadata / schema tolerance
# ---------------------------------------------------------------------------
def test_returns_reading_document_kind_manga(tmp_path):
    ref = _write_ref(tmp_path, _mokuro([_page("001.jpg", [_block(["あいさつ"])])]), None)
    doc = load(ref)
    assert isinstance(doc, ReadingDocument)
    assert doc.kind == "manga"


def test_metadata_comes_from_ref_not_json(tmp_path):
    data = _mokuro([_page("001.jpg", [_block(["ほんぶん"])])], title="JsonTitle", volume="99")
    ref = _write_ref(tmp_path, data, None, title="RefSeries", volume="7")
    doc = load(ref)
    assert doc.title == "RefSeries"
    assert doc.series == "RefSeries"
    assert doc.episode == "7"


def test_per_page_version_drift_and_unknown_keys_tolerated(tmp_path):
    pages = [
        _page("001.jpg", [_block(["いちぺーじ"])], version="0.2.0", surprise_page_key=1),
        _page("002.jpg", [_block(["にぺーじ"])], version="0.2.4"),
    ]
    data = _mokuro(pages, version="0.2.4", unknown_top_level="ignore me")
    doc = load(_write_ref(tmp_path, data, None))
    assert [u.text for u in doc.units] == ["いちぺーじ", "にぺーじ"]


def test_box_out_of_bounds_tolerated(tmp_path):
    # img is 800x1200 but the box far exceeds it — parse must not error.
    block = _block(["はみだし"], box=[-50, -50, 99999, 99999])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert [u.text for u in doc.units] == ["はみだし"]


def test_float_font_size_tolerated(tmp_path):
    block = _block(["ふぉんと"], font_size=23.5)
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert [u.text for u in doc.units] == ["ふぉんと"]


# ---------------------------------------------------------------------------
# Text assembly / sanitization
# ---------------------------------------------------------------------------
def test_lines_joined_with_empty_string(tmp_path):
    block = _block(["わた", "しは", "がくせい"])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert doc.units[0].text == "わたしはがくせい"


def test_falsy_lines_dropped_before_join(tmp_path):
    block = _block(["ねこ", "", "いぬ"])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert doc.units[0].text == "ねこいぬ"


def test_fullwidth_text_preserved_nfc_not_nfkc(tmp_path):
    # NFC keeps U+FF15 full-width 5; NFKC would fold it to ASCII. Assert we DON'T.
    block = _block(["５さい"])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert doc.units[0].text == "５さい"


def test_nfc_composes_combining_marks(tmp_path):
    # か + combining dakuten (U+3099) -> composed が (U+304C) after NFC.
    decomposed = "がわいい"
    expected = "がわいい"
    assert decomposed != expected  # the compose step must actually change it
    block = _block([decomposed])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert doc.units[0].text == expected


def test_zero_width_and_control_chars_stripped(tmp_path):
    # ZWSP (U+200B), BOM/ZWNBSP (U+FEFF) and a TAB control char are dropped.
    dirty = "こん​に﻿ち\tは"
    block = _block([dirty])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert doc.units[0].text == "こんにちは"


def test_junk_blocks_dropped(tmp_path):
    pages = [
        _page(
            "001.jpg",
            [
                _block(["あ"]),  # < 2 chars
                _block(["ab"]),  # no Japanese
                _block(["…"]),  # ellipsis only, no Japanese, < 2
                _block(["   "]),  # whitespace only
                _block([]),  # empty block
                _block(["まとも"]),  # the one keeper
            ],
        )
    ]
    doc = load(_write_ref(tmp_path, _mokuro(pages), None))
    assert [u.text for u in doc.units] == ["まとも"]


def test_repeat_run_over_8_collapsed_boundary_of_8_kept(tmp_path):
    pages = [
        _page("001.jpg", [_block(["そ" + "ー" * 9 + "だ"])]),  # 9 -> collapse
        _page("002.jpg", [_block(["そ" + "ー" * 8 + "だ"])]),  # 8 -> keep
    ]
    doc = load(_write_ref(tmp_path, _mokuro(pages), None))
    assert doc.units[0].text == "そーだ"
    assert doc.units[1].text == "そ" + "ー" * 8 + "だ"


def test_emphatic_double_not_collapsed(tmp_path):
    block = _block(["やめてッッ"])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert doc.units[0].text == "やめてッッ"


def test_long_repeat_then_survives_min_length(tmp_path):
    block = _block(["あ" + "ん" * 20])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert doc.units[0].text == "あん"


# ---------------------------------------------------------------------------
# Block split threshold
# ---------------------------------------------------------------------------
def test_under_threshold_block_stays_one_unit(tmp_path):
    block = _block(["はい。いいえ。またね。"])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert [u.text for u in doc.units] == ["はい。いいえ。またね。"]


def test_over_threshold_block_splits_on_adjacent_quotes(tmp_path):
    piece = "「せりふ。」"  # 6 chars, terminator lives inside the quote
    joined = piece * 30  # 180 chars > threshold
    assert len(joined) > _BLOCK_SPLIT_THRESHOLD
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [_block([joined])])]), None))
    assert len(doc.units) == 30
    assert all(u.text == piece for u in doc.units)
    # Split fallback units share the page label and take running indexes.
    assert [u.location_label for u in doc.units] == ["p.1"] * 30
    assert [u.index for u in doc.units] == list(range(30))


def test_over_threshold_unbalanced_bracket_block_splits(tmp_path):
    # Regression for the manga "wall of text" bug: an over-threshold block with
    # an unmatched leading 「 (rampant in OCR'd cover blurbs) used to survive
    # whole because the open bracket suppressed every internal 。. It must now
    # split into short mineable units, none equal to the full block.
    joined = "「" + "あいうえお。" * 21  # 127 chars > threshold, unmatched 「
    assert len(joined) > _BLOCK_SPLIT_THRESHOLD
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [_block([joined])])]), None))
    assert len(doc.units) > 1
    assert all(u.text != joined for u in doc.units)
    assert all(len(u.text) <= _BLOCK_SPLIT_THRESHOLD for u in doc.units)


# ---------------------------------------------------------------------------
# Block bounding boxes (block_box)
# ---------------------------------------------------------------------------
def test_unit_carries_block_box(tmp_path):
    block = _block(["ふきだし"], box=[10, 20, 110, 220])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert [u.block_box for u in doc.units] == [(10, 20, 110, 220)]


def test_split_pieces_share_parent_block_box(tmp_path):
    piece = "「せりふ。」"
    joined = piece * 30  # over threshold -> sentence-split
    assert len(joined) > _BLOCK_SPLIT_THRESHOLD
    block = _block([joined], box=[5, 6, 700, 800])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert len(doc.units) == 30
    assert all(u.block_box == (5, 6, 700, 800) for u in doc.units)


def test_missing_box_yields_none(tmp_path):
    block = _block(["はこなし"])
    del block["box"]
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert [u.block_box for u in doc.units] == [None]


def test_malformed_box_yields_none(tmp_path):
    null_box = _block(["ぬる"])
    null_box["box"] = None  # explicit null (the fixture's `box or default` would swallow it)
    blocks = [
        _block(["みじかい"], box=[1, 2, 3]),  # 3 elements
        _block(["もじれつ"], box=["a", "b", "c", "d"]),  # non-numeric
        null_box,
        _block(["すから"], box="0,0,10,10"),  # wrong type
    ]
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", blocks)]), None))
    assert [u.block_box for u in doc.units] == [None, None, None, None]


def test_float_box_coerced_to_ints(tmp_path):
    block = _block(["こてい"], box=[1.9, 2.1, 100.5, 200.0])
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [block])]), None))
    assert [u.block_box for u in doc.units] == [(1, 2, 100, 200)]


# ---------------------------------------------------------------------------
# Ordering / indexing / labels
# ---------------------------------------------------------------------------
def test_pages_and_blocks_keep_file_order(tmp_path):
    pages = [
        _page("z.jpg", [_block(["だいいち"]), _block(["だいに"])]),
        _page("a.jpg", [_block(["だいさん"])]),
    ]
    doc = load(_write_ref(tmp_path, _mokuro(pages), None))
    assert [u.text for u in doc.units] == ["だいいち", "だいに", "だいさん"]
    assert [u.index for u in doc.units] == [0, 1, 2]
    assert [u.location_label for u in doc.units] == ["p.1", "p.1", "p.2"]


# ---------------------------------------------------------------------------
# Text-only volume
# ---------------------------------------------------------------------------
def test_text_only_volume_warns_once_no_image_refs(tmp_path):
    pages = [_page("001.jpg", [_block(["てきすと"])]), _page("002.jpg", [_block(["のみ"])])]
    doc = load(_write_ref(tmp_path, _mokuro(pages), None))
    assert all(u.image_ref is None for u in doc.units)
    text_only = [w for w in doc.warnings if "text-only" in w]
    assert len(text_only) == 1
    # No per-page unmatched warnings when there is no image root at all.
    assert not any("no image matched" in w for w in doc.warnings)


# ---------------------------------------------------------------------------
# Image pairing — directory
# ---------------------------------------------------------------------------
def _mkimg(root: Path, rel: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_IMG_BYTES)
    return path


def test_pairing_tier1_exact_with_case_and_nfc_fold(tmp_path):
    img_root = tmp_path / "imgs"
    # Disk file stored NFC + mixed case; page img_path is NFD + different case.
    on_disk = _mkimg(img_root, "Page が.PNG")  # precomposed が
    nfd_name = unicodedata.normalize("NFD", "page が.png")  # decomposed か + mark
    assert nfd_name != "page が.png"  # ensure the fold is actually exercised
    doc = load(_write_ref(tmp_path, _mokuro([_page(nfd_name, [_block(["ほんぶん"])])]), img_root))
    assert doc.units[0].image_ref == ImageRef(on_disk)


def test_pairing_tier1_exact_with_subdir(tmp_path):
    img_root = tmp_path / "imgs"
    on_disk = _mkimg(img_root, "chapter1/001.jpg")
    doc = load(_write_ref(tmp_path, _mokuro([_page("chapter1/001.jpg", [_block(["ほん"])])]), img_root))
    assert doc.units[0].image_ref == ImageRef(on_disk)


def test_pairing_tier2_stem_when_extension_differs(tmp_path):
    img_root = tmp_path / "imgs"
    on_disk = _mkimg(img_root, "001.png")  # page says .jpg
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [_block(["ほん"])])]), img_root))
    assert doc.units[0].image_ref == ImageRef(on_disk)


def test_pairing_tier3_positional_when_counts_match(tmp_path):
    img_root = tmp_path / "imgs"
    # Names share neither full path nor stem; counts match -> natural-sort pair.
    p1 = _mkimg(img_root, "p001.png")
    p2 = _mkimg(img_root, "p002.png")
    pages = [
        _page("z_first.jpg", [_block(["さいしょ"])]),
        _page("a_second.jpg", [_block(["つぎ"])]),
    ]
    doc = load(_write_ref(tmp_path, _mokuro(pages), img_root))
    # natural sort: pages -> [a_second(idx1), z_first(idx0)]; imgs -> [p001, p002]
    assert doc.units[0].image_ref == ImageRef(p2)  # z_first (doc order 0) -> p002
    assert doc.units[1].image_ref == ImageRef(p1)  # a_second -> p001


def test_pairing_no_positional_when_counts_differ(tmp_path):
    img_root = tmp_path / "imgs"
    _mkimg(img_root, "x.png")  # only one image; stem-matches page 1 only
    pages = [
        _page("x.jpg", [_block(["いち"])]),  # tier2 stem hit
        _page("y.jpg", [_block(["にー"])]),  # no match, no positional (2 != 1)
    ]
    doc = load(_write_ref(tmp_path, _mokuro(pages), img_root))
    assert doc.units[0].image_ref == ImageRef(img_root / "x.png")
    assert doc.units[1].image_ref is None
    assert any("page 2" in w and "no image matched" in w for w in doc.warnings)


def test_pairing_ambiguous_stem_not_matched(tmp_path):
    img_root = tmp_path / "imgs"
    # Two files share stem "001" -> stem tier disqualified; counts differ (1!=2)
    # so no positional either; page stays unmatched.
    _mkimg(img_root, "001.png")
    _mkimg(img_root, "001.webp")
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [_block(["ほん"])])]), img_root))
    assert doc.units[0].image_ref is None


def test_dir_junk_and_nonimage_files_ignored(tmp_path):
    img_root = tmp_path / "imgs"
    _mkimg(img_root, "__MACOSX/001.jpg")  # junk path
    _mkimg(img_root, "notes.txt")  # non-image (would break count parity)
    on_disk = _mkimg(img_root, "001.jpg")
    doc = load(_write_ref(tmp_path, _mokuro([_page("001.jpg", [_block(["ほん"])])]), img_root))
    assert doc.units[0].image_ref == ImageRef(on_disk)


# ---------------------------------------------------------------------------
# Image pairing — archive (namelist only, no extraction)
# ---------------------------------------------------------------------------
def test_archive_imageref_without_extraction(tmp_path, monkeypatch):
    archive = tmp_path / "vol.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("001.jpg", _IMG_BYTES)
        zf.writestr("002.jpg", _IMG_BYTES)
        zf.writestr("__MACOSX/003.jpg", _IMG_BYTES)  # junk, must be skipped

    # Any byte-reading path is a bug: only namelist() is allowed at load time.
    def _forbidden(*_a, **_k):
        raise AssertionError("archive extraction attempted")

    monkeypatch.setattr(zipfile.ZipFile, "extract", _forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", _forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "read", _forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "open", _forbidden)

    pages = [
        _page("001.jpg", [_block(["いちまい"])]),
        _page("002.jpg", [_block(["にまい"])]),
    ]
    doc = load(_write_ref(tmp_path, _mokuro(pages), archive))

    assert doc.units[0].image_ref == ImageRef(archive, "001.jpg")
    assert doc.units[1].image_ref == ImageRef(archive, "002.jpg")
    # No files extracted to disk beyond the two we created ourselves.
    assert {p.name for p in tmp_path.iterdir()} == {"vol.mokuro", "vol.cbz"}


def test_archive_unmatched_page_warns(tmp_path):
    archive = tmp_path / "vol.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("aaa.jpg", _IMG_BYTES)
    pages = [
        _page("aaa.jpg", [_block(["あたり"])]),
        _page("zzz.jpg", [_block(["はずれ"])]),  # 2 pages vs 1 img -> no positional
    ]
    doc = load(_write_ref(tmp_path, _mokuro(pages), archive))
    assert doc.units[0].image_ref == ImageRef(archive, "aaa.jpg")
    assert doc.units[1].image_ref is None
    assert any("no image matched" in w for w in doc.warnings)

"""Tests for the Aozora/plain-text novel loader."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.reading.aozora_source import (
    _decode,
    _gaiji_char,
    _resolve_gaiji,
    _strip_ruby,
    load,
)
from anki_miner.services.reading.models import ReadingSourceRef


def _ref(path: Path, title: str | None = None) -> ReadingSourceRef:
    return ReadingSourceRef(
        kind="txt",
        path=path,
        image_root=None,
        title=title if title is not None else path.stem,
        volume=None,
    )


def _write(tmp_path: Path, text: str, encoding: str, name: str = "novel.txt") -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return p


# --- decode --------------------------------------------------------------


def test_decode_utf8_bom_stripped():
    raw = "﻿こんにちは".encode()
    assert raw[:3] == b"\xef\xbb\xbf"
    assert _decode(raw) == "こんにちは"


def test_decode_plain_utf8():
    assert _decode("日本語".encode()) == "日本語"


def test_decode_cp932_fallback():
    assert _decode("日本語".encode("cp932")) == "日本語"


def test_decode_euc_jp_tiebreak_when_cp932_raises():
    # This byte sequence is invalid cp932 but valid euc_jp -> euc_jp wins.
    raw = "日本語のテスト文章です".encode("euc_jp")
    assert _decode(raw) == "日本語のテスト文章です"


# --- gaiji ---------------------------------------------------------------


def test_gaiji_menkuten_plane1():
    # 1-85-73 via euc_jis_2004 recipe.
    assert _gaiji_char("「木＋温のつくり」、第3水準1-85-73") == "棈"


def test_gaiji_menkuten_plane2():
    # 2-88-74 needs the \x8f (SS3) plane-2 prefix.
    assert _gaiji_char("「にんべん＋咢」、第4水準2-88-74") == "譃"


def test_gaiji_menkuten_fullwidth_digits():
    assert _gaiji_char("第３水準１－８５－７３") == "棈"


def test_gaiji_u_plus_form():
    assert _gaiji_char("「…」、U+6F60、…") == chr(0x6F60)


def test_gaiji_unresolvable_becomes_geta():
    assert _gaiji_char("「変な記号」") == "〓"


def test_resolve_gaiji_inline():
    assert _resolve_gaiji("彼は※［＃「木＋温のつくり」、第3水準1-85-73］の木") == "彼は棈の木"


# --- ruby ----------------------------------------------------------------


def test_ruby_strip_without_bar():
    assert _strip_ruby("国境《くにざかい》の") == "国境の"


def test_ruby_strip_with_bar_mixed_base():
    assert _strip_ruby("｜長いトンネル《ながいトンネル》を") == "長いトンネルを"


def test_ruby_strip_multiple_spans():
    assert _strip_ruby("峠《とうげ》と国境《くにざかい》") == "峠と国境"


# --- full Aozora document (cp932) ---------------------------------------

_AOZORA = "\n".join(
    [
        "桜の森の満開の下",
        "坂口安吾",
        "",
        "-------------------------------------------------------",
        "【テキスト中に現れる記号について】",
        "",
        "《》：ルビ",
        "（例）峠《とうげ》",
        "-------------------------------------------------------",
        "",
        "第一章［＃「第一章」は大見出し］",
        "　国境《くにざかい》の｜長いトンネル《ながいトンネル》を抜けると雪国であった。",
        "　彼は※［＃「木＋温のつくり」、第3水準1-85-73］の木を見た。",
        "　［＃改ページ］",
        "　次の場面。",
        "",
        "底本：「日本文学全集」筑摩書房",
        "　　　1970（昭和45）年発行",
        "青空文庫作成ファイル：",
        "このファイルは……",
    ]
)


def test_aozora_cp932_full(tmp_path):
    p = _write(tmp_path, _AOZORA, "cp932")
    doc = load(_ref(p))

    assert doc.series == "Books"
    assert doc.kind == "book"
    # Header title becomes episode/title, not the file stem.
    assert doc.title == "桜の森の満開の下"
    assert doc.episode == "桜の森の満開の下"

    texts = [u.text for u in doc.units]
    assert texts == [
        "第一章",
        "国境の長いトンネルを抜けると雪国であった。",
        "彼は棈の木を見た。",
        "次の場面。",
    ]
    # Heading sets the chapter label for itself and following paragraphs.
    assert [u.location_label for u in doc.units] == ["第一章"] * 4
    # Running index and no cover.
    assert [u.index for u in doc.units] == [0, 1, 2, 3]
    assert all(u.image_ref is None for u in doc.units)
    # Colophon (底本 / 青空文庫作成ファイル) is cut.
    assert all("底本" not in u.text for u in doc.units)


def test_aozora_footer_cut_and_symbol_block_dropped(tmp_path):
    p = _write(tmp_path, _AOZORA, "cp932")
    doc = load(_ref(p))
    joined = "".join(u.text for u in doc.units)
    assert "記号について" not in joined  # symbol-explanation block dropped
    assert "ルビ" not in joined
    assert "筑摩書房" not in joined  # footer removed


# --- gaiji edge cases through load --------------------------------------


def test_load_plane2_and_uplus_and_unresolvable(tmp_path):
    text = "\n".join(
        [
            "見本",
            "著者",
            "",
            "　※［＃「にんべん＋咢」、第4水準2-88-74］と※［＃「…」、U+6F60］と※［＃「謎」］。",
        ]
    )
    p = _write(tmp_path, text, "cp932")
    doc = load(_ref(p))
    body = "".join(u.text for u in doc.units)
    assert "譃" in body
    assert chr(0x6F60) in body
    assert "〓" in body


# --- annotations ---------------------------------------------------------


def test_nested_bracket_annotation_scanner(tmp_path):
    # A ］ inside a 「」 span must not close the annotation early (regex would).
    text = "\n".join(
        [
            "題名",
            "著者",
            "",
            "本文［＃「テスト］記号」は太字］の続き。",
        ]
    )
    p = _write(tmp_path, text, "cp932")
    doc = load(_ref(p))
    assert [u.text for u in doc.units] == ["本文の続き。"]


def test_bouten_keeps_base_text(tmp_path):
    text = "\n".join(["題名", "著者", "", "大丈夫［＃「大丈夫」に傍点］だ。"])
    p = _write(tmp_path, text, "cp932")
    doc = load(_ref(p))
    assert [u.text for u in doc.units] == ["大丈夫だ。"]


def test_kaipage_produces_no_unit(tmp_path):
    text = "\n".join(["題名", "著者", "", "前。", "　［＃改ページ］", "後。", "　［＃改丁］"])
    p = _write(tmp_path, text, "cp932")
    doc = load(_ref(p))
    assert [u.text for u in doc.units] == ["前。", "後。"]


def test_block_heading_form(tmp_path):
    text = "\n".join(
        [
            "題名",
            "著者",
            "",
            "［＃ここから大見出し］",
            "序章",
            "［＃ここで大見出し終わり］",
            "本文だ。",
        ]
    )
    p = _write(tmp_path, text, "cp932")
    doc = load(_ref(p))
    assert [u.text for u in doc.units] == ["序章", "本文だ。"]
    assert [u.location_label for u in doc.units] == ["序章", "序章"]


# --- plain text ----------------------------------------------------------


def test_plain_utf8_path(tmp_path):
    text = "これは普通のテキストです。二文目もある。\n\n次の段落。"
    p = _write(tmp_path, text, "utf-8", name="mynovel.txt")
    doc = load(_ref(p, title="mynovel"))

    assert doc.series == "Books"
    assert doc.kind == "book"
    # No Aozora header -> title stays the provisional ref.title (file stem).
    assert doc.title == "mynovel"
    assert doc.episode == "mynovel"

    assert [u.text for u in doc.units] == [
        "これは普通のテキストです。",
        "二文目もある。",
        "次の段落。",
    ]
    assert [u.location_label for u in doc.units] == ["¶1", "¶1", "¶2"]
    assert [u.index for u in doc.units] == [0, 1, 2]
    assert all(u.image_ref is None for u in doc.units)


def test_plain_strips_colophon(tmp_path):
    text = "本文の一行目。\n本文の二行目。\n底本：「どこかの本」出版社\n入力：誰か"
    p = _write(tmp_path, text, "utf-8")
    doc = load(_ref(p))
    joined = "".join(u.text for u in doc.units)
    assert "底本" not in joined
    assert "入力" not in joined
    assert [u.text for u in doc.units] == ["本文の一行目。", "本文の二行目。"]


def test_bom_utf8_document(tmp_path):
    text = "﻿先頭にBOMがある。\n次の行。"
    p = tmp_path / "bom.txt"
    p.write_bytes(text.encode("utf-8"))
    doc = load(_ref(p, title="bom"))
    assert [u.text for u in doc.units] == ["先頭にBOMがある。", "次の行。"]


# --- shared-splitter fix: unmatched brackets no longer suppress splitting ----


def test_unmatched_opener_paragraph_splits_into_sentences(tmp_path):
    # A narrative paragraph whose 「 is never closed used to collapse into one
    # over-long unit (same "wall of text" defect as manga); it now splits on
    # the internal 。.
    p = _write(tmp_path, "「あの人は言った。それから去った", "utf-8")
    doc = load(_ref(p))
    assert [u.text for u in doc.units] == ["「あの人は言った。", "それから去った"]


def test_balanced_quote_with_attribution_stays_one_unit(tmp_path):
    # A matched 「」 still suppresses its internal terminator: quote plus
    # attribution remains a single mining unit (no over-splitting).
    p = _write(tmp_path, "「行くぞ。」と彼は言った。", "utf-8")
    doc = load(_ref(p))
    assert [u.text for u in doc.units] == ["「行くぞ。」と彼は言った。"]


# --- Bug Y4: bare 《…》 must not misclassify a plain novel as Aozora ---------


def test_standalone_double_angle_not_treated_as_aozora(tmp_path):
    # A plain novel that writes a work title with the double-angle bracket
    # (《作品名》) — 《 stands alone (line-start / after whitespace), NOT attached
    # to a kanji/kana base as ruby is. It must NOT be read as Aozora: the first
    # block stays and the 《…》 text is preserved verbatim (no ruby strip, no
    # header drop).
    text = "\n".join(["《作品名》は面白い。", "二行目もある。"])
    p = _write(tmp_path, text, "utf-8", name="plain.txt")
    doc = load(_ref(p, title="plain"))
    assert doc.title == "plain"  # provisional ref title, no header extraction
    assert [u.text for u in doc.units] == ["《作品名》は面白い。", "二行目もある。"]


def test_ruby_attached_base_detected_as_aozora(tmp_path):
    # Ruby attached to a kanji base (山道《やまみち》) is a genuine Aozora signal
    # even without a ruler or ［＃ annotation: the file is detected, the header
    # extracted, and the reading stripped.
    text = "\n".join(["峠の物語", "著者", "", "　山道《やまみち》を歩いた。"])
    p = _write(tmp_path, text, "utf-8", name="ruby.txt")
    doc = load(_ref(p, title="ruby"))
    assert doc.title == "峠の物語"  # Aozora path extracts the header title
    assert [u.text for u in doc.units] == ["山道を歩いた。"]  # ruby reading gone

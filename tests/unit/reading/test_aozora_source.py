"""Tests for the Aozora/plain-text novel loader."""

from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.languages.registry import get_profile
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.services.reading import aozora_source, detector
from anki_miner.services.reading.aozora_source import (
    _decode,
    _extract_header,
    _gaiji_char,
    _resolve_gaiji,
    _strip_ruby,
    load,
)


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


def test_surrogate_gaiji_becomes_geta():
    assert _gaiji_char("U+D800") == "〓"


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


# --- header --------------------------------------------------------------


def test_extract_header_skips_leading_blank_lines():
    lines = ["題名", "著者", "", "本文。"]
    expected = ("題名", ["本文。"])

    assert _extract_header(["", *lines]) == expected
    assert _extract_header(lines) == expected


@pytest.mark.parametrize("lines", [[], ["", "  ", "\t"]])
def test_extract_header_empty_content(lines):
    assert _extract_header(lines) == ("", [])


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


def test_oversized_novel_file_fails_cleanly(tmp_path, monkeypatch):
    path = _write(tmp_path, "本文です。", "utf-8")
    monkeypatch.setattr(aozora_source, "_MAX_TEXT_FILE_BYTES", 4, raising=False)

    with pytest.raises(SetupError, match="too large to mine"):
        load(_ref(path))


def test_novel_file_growth_after_stat_uses_bounded_read(monkeypatch):
    path = MagicMock(spec=Path)
    path.name = "novel.txt"
    path.stem = "novel"
    path.stat.return_value = SimpleNamespace(st_size=1)
    path.read_bytes.return_value = b"x" * 5
    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader.read.side_effect = lambda size: b"x" * size
    path.open.return_value = reader
    monkeypatch.setattr(aozora_source, "_MAX_TEXT_FILE_BYTES", 4, raising=False)

    with pytest.raises(SetupError, match="too large to mine"):
        load(_ref(path))  # type: ignore[arg-type]

    reader.read.assert_called_once_with(5)


def test_aozora_footer_cut_and_symbol_block_dropped(tmp_path):
    p = _write(tmp_path, _AOZORA, "cp932")
    doc = load(_ref(p))
    joined = "".join(u.text for u in doc.units)
    assert "記号について" not in joined  # symbol-explanation block dropped
    assert "ルビ" not in joined
    assert "筑摩書房" not in joined  # footer removed


# --- parts of an over-cap file ------------------------------------------


def _shrink_parts(monkeypatch, *, cap: int, part: int, slack: int) -> None:
    """Shrink the cap, part size and line slack so a small file splits."""
    assert slack < part and 2 * part <= cap  # the module's own invariants
    monkeypatch.setattr(aozora_source, "_MAX_TEXT_FILE_BYTES", cap)
    monkeypatch.setattr(aozora_source, "_PART_BYTES", part)
    monkeypatch.setattr(aozora_source, "_LINE_SLACK", slack)


def _read_at_cuts(path: Path, cuts: list[int]) -> list[bytes]:
    """Read the parts starting at each cut (cuts[0] == 0), the last one open-ended."""
    with path.open("rb") as f:
        return [
            aozora_source._read_part(f, path.name, start, end)
            for start, end in zip(cuts, [*cuts[1:], None], strict=True)
        ]


def test_parts_rejoin_to_the_file_on_line_boundaries(tmp_path, monkeypatch):
    slack = 24
    _shrink_parts(monkeypatch, cap=10_000, part=64, slack=slack)
    path = tmp_path / "corpus.txt"
    for seed in range(300):
        rng = random.Random(seed)
        newline = rng.choice(["\n", "\r\n"])
        bom = "﻿" if rng.random() < 0.2 else ""
        lines = []
        for _ in range(rng.randint(0, 40)):
            # Up to exactly `slack` bytes with the newline (and the first line's
            # BOM), in 1-3 byte characters.
            line = "" if lines else bom
            while True:
                char = rng.choice("ab漢字あ。 ")
                if len((line + char + newline).encode()) > slack:
                    break
                line += char
                if rng.random() < 0.15:
                    break
            lines.append(line)
        text = newline.join(lines) + (newline if lines and rng.random() < 0.5 else "")
        data = text.encode()
        path.write_bytes(data)
        line_starts = [0] + [i + 1 for i, b in enumerate(data) if b == 0x0A]
        pool = list(range(1, len(data))) + line_starts[1:]
        cuts = [0, *sorted(set(rng.sample(pool, min(len(pool), rng.randint(0, 12)))))]

        parts = _read_at_cuts(path, cuts)

        assert b"".join(parts) == data, f"seed {seed}"
        offset = 0
        for part in parts:
            if part:
                assert offset in line_starts, f"seed {seed}: part at {offset} splits a line"
            offset += len(part)


def test_parts_mine_the_same_units_as_the_whole_file(tmp_path, monkeypatch):
    text = "\n".join(f"{i}行目の文です。次の文です。" for i in range(200)) + "\n"
    path = _write(tmp_path, text, "utf-8", name="big.txt")
    whole = [u.text for u in load(_ref(path)).units]
    _shrink_parts(monkeypatch, cap=4096, part=512, slack=128)

    refs = aozora_source.split_oversize([_ref(path)])

    size = path.stat().st_size
    count = size // 512
    assert len(refs) == count
    assert [r.title for r in refs] == [f"big ({i}/{count})" for i in range(1, count + 1)]
    assert [r.byte_range for r in refs] == [(i * 512, (i + 1) * 512) for i in range(count - 1)] + [
        ((count - 1) * 512, None)
    ]
    with pytest.raises(SetupError, match="too large to mine"):
        load(_ref(path))  # the whole file is over the cap; every part is not
    docs = [load(r) for r in refs]
    assert [u.text for doc in docs for u in doc.units] == whole
    assert [doc.episode for doc in docs] == [r.title for r in refs]


def test_aozora_corpus_parts_keep_whole_file_header_and_colophon_rules(tmp_path, monkeypatch):
    # Five books in one file: whole-file semantics drop only the first header
    # and cut only after the LAST 底本 colophon. A per-part cut would lose the
    # opening of every book after a part's last colophon.
    path = _write(tmp_path, "\n".join([_AOZORA] * 5), "utf-8", name="corpus.txt")
    whole = [u.text for u in load(_ref(path)).units]
    _shrink_parts(monkeypatch, cap=1024, part=256, slack=200)

    refs = aozora_source.split_oversize([_ref(path)])
    docs = [load(r) for r in refs]

    assert len(refs) > 2
    assert [u.text for doc in docs for u in doc.units] == whole
    assert "桜の森の満開の下" in whole  # later books' headers are body text, as on the whole file
    assert docs[0].title == f"corpus (1/{len(refs)})"  # a part never takes the header title


def test_long_boundary_line_fails_only_the_two_parts_beside_it(tmp_path, monkeypatch):
    _shrink_parts(monkeypatch, cap=10_000, part=16, slack=8)
    path = tmp_path / "long.txt"
    # "b"*30 spans offsets 20..50 and crosses the cut at 32.
    path.write_bytes(b"aaaa\n" * 4 + b"b" * 30 + b"\n" + b"cccc\n" * 8)

    with path.open("rb") as f:
        assert aozora_source._read_part(f, "long.txt", 0, 16) == b"aaaa\n" * 4
        with pytest.raises(SetupError, match="line over 1 MB"):
            aozora_source._read_part(f, "long.txt", 16, 32)
        with pytest.raises(SetupError, match="line over 1 MB"):
            aozora_source._read_part(f, "long.txt", 32, 48)
        assert aozora_source._read_part(f, "long.txt", 48, None) == b"cccc\n" * 8


def test_boundary_line_ending_exactly_at_the_slack_is_skipped_as_it_is_kept(tmp_path, monkeypatch):
    # Both sides must stop on the same byte: the part before reads exactly
    # `slack` bytes past the cut and keeps the line, so the part after must
    # skip it rather than call it too long.
    _shrink_parts(monkeypatch, cap=10_000, part=16, slack=8)
    path = tmp_path / "edge.txt"
    path.write_bytes(b"aaa\n" + b"b" * 11 + b"\n" + b"cc\n")  # cut at 8 leaves 8 bytes of the b-line

    assert _read_at_cuts(path, [0, 8]) == [b"aaa\n" + b"b" * 11 + b"\n", b"cc\n"]


def test_last_part_takes_text_appended_after_the_split(tmp_path, monkeypatch):
    path = _write(tmp_path, "本文の文です。\n" * 60, "utf-8", name="grow.txt")
    _shrink_parts(monkeypatch, cap=512, part=128, slack=64)
    refs = aozora_source.split_oversize([_ref(path)])
    with path.open("ab") as f:
        f.write("追記された文です。\n".encode())

    assert load(refs[-1]).units[-1].text == "追記された文です。"


def test_split_oversize_passes_through_what_it_cannot_or_need_not_split(tmp_path, monkeypatch):
    _shrink_parts(monkeypatch, cap=64, part=16, slack=8)
    small = _write(tmp_path, "短い。\n", "utf-8", name="small.txt")
    utf16 = _write(tmp_path, "﻿" + "長い文です。\n" * 20, "utf-16-le", name="wide.txt")
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"x" * 200)
    refs = [
        _ref(small),
        _ref(utf16),  # a b"\n" split is unsafe in UTF-16
        _ref(tmp_path / "missing.txt"),  # the loader reports it when the item runs
        ReadingSourceRef(kind="epub", path=epub, title="book"),
    ]

    assert aozora_source.split_oversize(refs) == refs


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


def test_explicit_latin_base_ruby_detected_as_aozora(tmp_path):
    text = "\n".join(["題名", "著者", "", "｜JavaScript《ジャバスクリプト》を学ぶ。"])
    p = _write(tmp_path, text, "utf-8", name="latin-ruby.txt")
    doc = load(_ref(p, title="latin-ruby"))
    assert doc.title == "題名"
    assert [u.text for u in doc.units] == ["JavaScriptを学ぶ。"]


# --- a Chinese 《书名》 is a work title, never attached ruby -----------------

_ZH_LINES = (
    "他昨天读了《红楼梦》，觉得很有意思。",
    "我也想看《西游记》。",
    "下周去买《水浒传》。",
)


def _zh_doc(tmp_path, text, name):
    """Load *text* the way Reading → Novels loads a Chinese novel."""
    p = _write(tmp_path, text, "utf-8", name=name)
    profile = get_profile("zh")
    (ref,) = detector.detect(p)
    return detector.load(
        ref,
        encodings=profile.import_encodings,
        rules=profile.sentence_rules,
    )


def test_chinese_titles_not_aozora_one_paragraph_per_line(tmp_path):
    # 了《红楼梦》 is a CJK character before 《 but the span holds a work title,
    # not a kana reading. Taking it for ruby made this layout — no blank line
    # anywhere — one long "header", and the file mined nothing at all.
    doc = _zh_doc(tmp_path, "\n".join(_ZH_LINES), "zh-novel.txt")
    assert doc.title == "zh-novel"  # provisional ref title, no header extraction
    assert [u.text for u in doc.units] == list(_ZH_LINES)


def test_chinese_titles_not_aozora_blank_separated(tmp_path):
    doc = _zh_doc(tmp_path, "\n\n".join(_ZH_LINES), "zh-spaced.txt")
    assert doc.title == "zh-spaced"
    assert [u.text for u in doc.units] == list(_ZH_LINES)


def test_chinese_fullwidth_bar_before_a_title_is_not_aozora(tmp_path):
    # ｜ separates a chapter label here; the 《…》 after it is still a work
    # title, so the ｜-base-marker branch needs the same kana reading.
    lines = ("第一章｜他读了《红楼梦》。", "然后就睡了。")
    doc = _zh_doc(tmp_path, "\n".join(lines), "zh-bar.txt")
    assert doc.title == "zh-bar"
    assert [u.text for u in doc.units] == list(lines)

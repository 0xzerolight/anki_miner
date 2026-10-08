"""text_cleanup.clean: reading order, furigana, page cursors, speaker names."""

from __future__ import annotations

import pytest

from anki_miner.services.video_ocr.meiki_engine import OcrBox
from anki_miner.services.video_ocr.text_cleanup import clean


def box(text: str, x1: int, y1: int, x2: int, y2: int) -> OcrBox:
    return OcrBox(text=text, bbox=(x1, y1, x2, y2), mean_conf=0.9)


def test_rows_read_top_to_bottom_and_boxes_left_to_right():
    boxes = [box("二行目", 0, 60, 300, 100), box("です", 320, 10, 400, 50), box("一行目", 0, 10, 300, 50)]
    assert clean(boxes) == "一行目です\n二行目"


def test_furigana_above_its_kanji_is_dropped():
    boxes = [box("かんじ", 10, 86, 70, 100), box("漢字を読む", 0, 100, 400, 140)]
    assert clean(boxes) == "漢字を読む"


@pytest.mark.parametrize("short", ["……", "ー"])
def test_a_short_real_line_with_normal_line_spacing_is_kept(short):
    boxes = [box(short, 0, 10, 60, 24), box("そうか", 0, 44, 200, 84)]
    assert clean(boxes) == f"{short}\nそうか"


def test_trailing_page_cursor_is_stripped():
    assert clean([box("行くぞ▼", 0, 0, 300, 40)]) == "行くぞ"


def test_name_plate_row_is_dropped():
    boxes = [box("クラウド", 0, 0, 160, 40), box("「行くぞ」", 0, 50, 300, 90)]
    assert clean(boxes) == "「行くぞ」"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("クラウド「行くぞ」", "「行くぞ」"),
        ("ティファ：待って", "待って"),
        ("社長:了解", "了解"),
        ("それは「愛」", "それは「愛」"),  # hiragana prefix: ordinary text, not a name
        ("クラウド「行くぞ」と言った", "クラウド「行くぞ」と言った"),  # bracket does not close the text
        ("「行くぞ」", "「行くぞ」"),
    ],
)
def test_inline_speaker_names(raw, expected):
    assert clean([box(raw, 0, 0, 600, 40)]) == expected


@pytest.mark.parametrize(("raw", "expected"), [("行くぞ!?", "行くぞ！？"), ("~だよね~", "〜だよね〜")])
def test_half_width_punctuation_from_the_recogniser_is_restored(raw, expected):
    # The recogniser's labels are NFKC-folded: it returns ASCII !?~ for Japanese ！？〜.
    assert clean([box(raw, 0, 0, 400, 40)]) == expected


def test_no_boxes_or_blank_boxes_mean_no_dialogue():
    assert clean([]) == ""
    assert clean([box("  ", 0, 0, 100, 40)]) == ""

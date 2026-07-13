"""Tests for per-kanji furigana distribution.

The ``distribute_furigana`` corpus below is ported line-for-line from Yomitan's
``test/japanese-util.test.js`` (the ``distributeFurigana`` describe block),
upstream commit ``e2ed450``. Segments are compared as ``(text, reading)``
tuples so the Python port must reproduce Yomitan's segmentation exactly,
including the deliberate whole-word fallback on ambiguous splits.
"""

import pytest

from anki_miner.utils.furigana_distribute import (
    FuriganaSegment,
    distribute_furigana,
    get_stem_length,
)

# (term, reading) -> [(text, reading), ...]
# Ported verbatim from test/japanese-util.test.js:279-737.
DISTRIBUTE_CASES = [
    (("有り難う", "ありがとう"), [("有", "あ"), ("り", ""), ("難", "がと"), ("う", "")]),
    (("方々", "かたがた"), [("方々", "かたがた")]),
    (("お祝い", "おいわい"), [("お", ""), ("祝", "いわ"), ("い", "")]),
    (("美味しい", "おいしい"), [("美味", "おい"), ("しい", "")]),
    (("食べ物", "たべもの"), [("食", "た"), ("べ", ""), ("物", "もの")]),
    (("試し切り", "ためしぎり"), [("試", "ため"), ("し", ""), ("切", "ぎ"), ("り", "")]),
    # Ambiguous -> whole-word fallback
    (("飼い犬", "かいいぬ"), [("飼い犬", "かいいぬ")]),
    (("長い間", "ながいあいだ"), [("長い間", "ながいあいだ")]),
    # Same / empty reading
    (("飼い犬", ""), [("飼い犬", "")]),
    (("かいいぬ", "かいいぬ"), [("かいいぬ", "")]),
    (("かいぬ", "かいぬ"), [("かいぬ", "")]),
    # Misc
    (("月", "か"), [("月", "か")]),
    (("月", "カ"), [("月", "カ")]),
    # Mismatched kana readings
    (("有り難う", "アリガトウ"), [("有", "ア"), ("り", "リ"), ("難", "ガト"), ("う", "ウ")]),
    (("ありがとう", "アリガトウ"), [("ありがとう", "アリガトウ")]),
    # Mismatched kana readings (real examples)
    (("カ月", "かげつ"), [("カ", "か"), ("月", "げつ")]),
    (("序ノ口", "じょのくち"), [("序", "じょ"), ("ノ", "の"), ("口", "くち")]),
    (("スズメの涙", "すずめのなみだ"), [("スズメ", "すずめ"), ("の", ""), ("涙", "なみだ")]),
    (("二カ所", "にかしょ"), [("二", "に"), ("カ", "か"), ("所", "しょ")]),
    (("八ツ橋", "やつはし"), [("八", "や"), ("ツ", "つ"), ("橋", "はし")]),
    (("八ツ橋", "やつはし"), [("八", "や"), ("ツ", "つ"), ("橋", "はし")]),
    (("一カ月", "いっかげつ"), [("一", "いっ"), ("カ", "か"), ("月", "げつ")]),
    (("一カ所", "いっかしょ"), [("一", "いっ"), ("カ", "か"), ("所", "しょ")]),
    (("カ所", "かしょ"), [("カ", "か"), ("所", "しょ")]),
    (("数カ月", "すうかげつ"), [("数", "すう"), ("カ", "か"), ("月", "げつ")]),
    (("くノ一", "くのいち"), [("く", ""), ("ノ", "の"), ("一", "いち")]),
    (("くノ一", "くのいち"), [("く", ""), ("ノ", "の"), ("一", "いち")]),
    (("数カ国", "すうかこく"), [("数", "すう"), ("カ", "か"), ("国", "こく")]),
    (("数カ所", "すうかしょ"), [("数", "すう"), ("カ", "か"), ("所", "しょ")]),
    (
        ("壇ノ浦の戦い", "だんのうらのたたかい"),
        [
            ("壇", "だん"),
            ("ノ", "の"),
            ("浦", "うら"),
            ("の", ""),
            ("戦", "たたか"),
            ("い", ""),
        ],
    ),
    (
        ("壇ノ浦の戦", "だんのうらのたたかい"),
        [("壇", "だん"), ("ノ", "の"), ("浦", "うら"), ("の", ""), ("戦", "たたかい")],
    ),
    (("序ノ口格", "じょのくちかく"), [("序", "じょ"), ("ノ", "の"), ("口格", "くちかく")]),
    (("二カ国語", "にかこくご"), [("二", "に"), ("カ", "か"), ("国語", "こくご")]),
    (("カ国", "かこく"), [("カ", "か"), ("国", "こく")]),
    (("カ国語", "かこくご"), [("カ", "か"), ("国語", "こくご")]),
    (
        ("壇ノ浦の合戦", "だんのうらのかっせん"),
        [("壇", "だん"), ("ノ", "の"), ("浦", "うら"), ("の", ""), ("合戦", "かっせん")],
    ),
    (("一タ偏", "いちたへん"), [("一", "いち"), ("タ", "た"), ("偏", "へん")]),
    (("ル又", "るまた"), [("ル", "る"), ("又", "また")]),
    (("ノ木偏", "のぎへん"), [("ノ", "の"), ("木偏", "ぎへん")]),
    (("一ノ貝", "いちのかい"), [("一", "いち"), ("ノ", "の"), ("貝", "かい")]),
    (("虎ノ門事件", "とらのもんじけん"), [("虎", "とら"), ("ノ", "の"), ("門事件", "もんじけん")]),
    (
        ("教育ニ関スル勅語", "きょういくにかんするちょくご"),
        [
            ("教育", "きょういく"),
            ("ニ", "に"),
            ("関", "かん"),
            ("スル", "する"),
            ("勅語", "ちょくご"),
        ],
    ),
    (("二カ年", "にかねん"), [("二", "に"), ("カ", "か"), ("年", "ねん")]),
    (("三カ年", "さんかねん"), [("三", "さん"), ("カ", "か"), ("年", "ねん")]),
    (("四カ年", "よんかねん"), [("四", "よん"), ("カ", "か"), ("年", "ねん")]),
    (("五カ年", "ごかねん"), [("五", "ご"), ("カ", "か"), ("年", "ねん")]),
    (("六カ年", "ろっかねん"), [("六", "ろっ"), ("カ", "か"), ("年", "ねん")]),
    (("七カ年", "ななかねん"), [("七", "なな"), ("カ", "か"), ("年", "ねん")]),
    (("八カ年", "はちかねん"), [("八", "はち"), ("カ", "か"), ("年", "ねん")]),
    (("九カ年", "きゅうかねん"), [("九", "きゅう"), ("カ", "か"), ("年", "ねん")]),
    (("十カ年", "じゅうかねん"), [("十", "じゅう"), ("カ", "か"), ("年", "ねん")]),
    (("鏡ノ間", "かがみのま"), [("鏡", "かがみ"), ("ノ", "の"), ("間", "ま")]),
    (("鏡ノ間", "かがみのま"), [("鏡", "かがみ"), ("ノ", "の"), ("間", "ま")]),
    (
        ("ページ違反", "ぺーじいはん"),
        [("ペ", "ぺ"), ("ー", ""), ("ジ", "じ"), ("違反", "いはん")],
    ),
    # Mismatched kana
    (("サボる", "サボル"), [("サボ", ""), ("る", "ル")]),
    # Reading starts with term, but has remainder characters
    (
        ("シック", "シック・ビルしょうこうぐん"),
        [("シック", "シック・ビルしょうこうぐん")],
    ),
    # Kanji distribution tests
    (("逸らす", "そらす"), [("逸", "そ"), ("らす", "")]),
    (("逸らす", "そらす"), [("逸", "そ"), ("らす", "")]),
]


@pytest.mark.parametrize("term_reading,expected", DISTRIBUTE_CASES)
def test_distribute_furigana(term_reading, expected):
    term, reading = term_reading
    actual = [(s.text, s.reading) for s in distribute_furigana(term, reading)]
    assert actual == expected


def test_distribute_furigana_returns_segment_objects():
    """The public return type is a list of FuriganaSegment(text, reading)."""
    segments = distribute_furigana("食べる", "たべる")
    assert all(isinstance(s, FuriganaSegment) for s in segments)
    assert segments[0].text == "食"
    assert segments[0].reading == "た"


class TestGetStemLength:
    """Common-prefix (codepoint) length, ported from getStemLength."""

    def test_identical(self):
        assert get_stem_length("たべる", "たべる") == 3

    def test_shared_prefix(self):
        assert get_stem_length("たべる", "たべた") == 2

    def test_no_shared_prefix(self):
        assert get_stem_length("あい", "うえ") == 0

    def test_empty_operand(self):
        assert get_stem_length("", "たべる") == 0
        assert get_stem_length("たべる", "") == 0

    def test_prefix_is_full_shorter_string(self):
        assert get_stem_length("たべ", "たべる") == 2

"""Golden table for the depth-gated sentence splitter."""

import pytest

from anki_miner.services.reading.sentence_splitter import split_sentences

# (label, text, kwargs, expected) golden cases.
_CASES = [
    (
        "basic-multi-terminator",
        "おはよう。元気？はい！",
        {},
        ["おはよう。", "元気？", "はい！"],
    ),
    (
        "quote-internal-punctuation",
        "彼は「行くぞ！」と言った。次へ。",
        {},
        ["彼は「行くぞ！」と言った。", "次へ。"],
    ),
    (
        "depth-2-nesting",
        "「彼が『やめろ。』と叫んだ。」終わり。次。",
        {},
        ["「彼が『やめろ。』と叫んだ。」終わり。", "次。"],
    ),
    (
        "ellipsis-run-then-terminator",
        "待って……。行こう。",
        {},
        ["待って……。", "行こう。"],
    ),
    (
        "fullwidth-dot-run-not-terminator",
        "これは．．．続く",
        {},
        ["これは．．．続く"],
    ),
    (
        "fullwidth-dot-run-with-terminator",
        "これは．．．。終わり",
        {},
        ["これは．．．。", "終わり"],
    ),
    (
        "lone-fullwidth-dot-terminates",
        "A．B",
        {},
        ["A．", "B"],
    ),
    (
        "adjacent-quotes-split-on",
        "「セリフ1」「セリフ2」",
        {"split_adjacent_quotes": True},
        ["「セリフ1」", "「セリフ2」"],
    ),
    (
        "adjacent-quotes-split-off",
        "「セリフ1」「セリフ2」",
        {},
        ["「セリフ1」「セリフ2」"],
    ),
    (
        "halfwidth-bang-question-run",
        "本当!?すごい!",
        {},
        ["本当!?", "すごい!"],
    ),
    (
        "fullwidth-bang-question-absorbed",
        "やめて！？本当？",
        {},
        ["やめて！？", "本当？"],
    ),
    (
        "accepted-false-split-morning-musume",
        "モー娘。のコンサート",
        {},
        ["モー娘。", "のコンサート"],
    ),
    (
        "unmatched-closer-ignored",
        "」だ。うん。",
        {},
        ["」だ。", "うん。"],
    ),
    (
        "unterminated-tail-flushed",
        "これは終わらない",
        {},
        ["これは終わらない"],
    ),
    (
        # An unmatched leading opener no longer suppresses the internal 。
        # (was the "wall of text" bug); the opener stays depth-neutral.
        "unmatched-open-leading",
        "「未完 だ。まだ",
        {},
        ["「未完 だ。", "まだ"],
    ),
    (
        "unmatched-open-midstring",
        "だめ。「やめて まだ",
        {},
        ["だめ。", "「やめて まだ"],
    ),
    (
        # 「あ」 is a matched pair (still gates depth); the trailing 『 is
        # unmatched, so the 。 after いだ fires.
        "mixed-unmatched-and-matched",
        "「あ」。『いだ。まだ",
        {},
        ["「あ」。", "『いだ。", "まだ"],
    ),
    (
        # Type-agnostic pairing: the lone 』 pops the nearest opener (『),
        # leaving the outer 「 unmatched, so the first 。 splits.
        "nested-unclosed-type-agnostic",
        "「あ。い『う』え",
        {},
        ["「あ。", "い『う』え"],
    ),
    (
        # A genuinely matched quote still suppresses its internal terminators.
        "balanced-multi-sentence-quote-no-split",
        "「行くぞ。帰るぞ。」と言った。",
        {},
        ["「行くぞ。帰るぞ。」と言った。"],
    ),
    (
        # The closer branch (incl. the 」「 adjacent break) is untouched: an
        # unmatched 」 followed by 「 still breaks under split_adjacent_quotes.
        "unmatched-closer-adjacent-break",
        "あ」「い",
        {"split_adjacent_quotes": True},
        ["あ」", "「い"],
    ),
    (
        "halfwidth-paren-pair",
        "計算(a+b)=c。",
        {},
        ["計算(a+b)=c。"],
    ),
    (
        "trailing-whitespace-dropped",
        "A。   ",
        {},
        ["A。"],
    ),
]


@pytest.mark.parametrize(
    "text,kwargs,expected",
    [(c[1], c[2], c[3]) for c in _CASES],
    ids=[c[0] for c in _CASES],
)
def test_split_sentences_golden(text, kwargs, expected):
    assert split_sentences(text, **kwargs) == expected


def test_empty_and_whitespace_only_dropped():
    assert split_sentences("") == []
    assert split_sentences("   \n\t ") == []


def test_adjacent_quotes_default_is_false():
    # Positional-only signature: kwarg is keyword-only; default must not split.
    assert split_sentences("「あ」「い」") == ["「あ」「い」"]


def test_real_mokuro_cover_blurb_no_longer_one_wall():
    # The actual Horimiya back-cover blurb (Issue: manga Sentence field was a
    # full-page wall). Two unmatched openers (『 at pos 0, 「 mid-string) used to
    # suppress every internal terminator; now it splits on 。 / ．．．！？.
    blurb = (
        "『家庭的な女子高生・堀さんと、しかし、それは、学校では敬語地味イカスだけど、"
        "実はアニメーション、１０個土地に、スだらけの美形男子「宮村くん。"
        "真逆のようで似ているようなあと、夫はヒノへにちりの美形二人が偶然出会ったら．．．！？"
        "また、ネットのいるは甘くて胸がキュッとなる、だ、いやハーキンとなる、"
        "超常炭酸系スクールライフ第１巻／"
    )
    pieces = split_sentences(blurb, split_adjacent_quotes=True)
    assert len(pieces) >= 2
    # No piece is the whole wall, and each is a normal-length sentence.
    assert all(len(p) < len(blurb) for p in pieces)
    assert max(len(p) for p in pieces) < 120

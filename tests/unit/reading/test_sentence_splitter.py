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
        "unbalanced-open-contained",
        "「未完 だ。まだ",
        {},
        ["「未完 だ。まだ"],
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

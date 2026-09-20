"""S9: Thai reading-tab paragraphs split on whitespace runs."""

from __future__ import annotations

from anki_miner.languages.profile import SentenceRules
from anki_miner.languages.registry import get_profile
from anki_miner.services.reading.sentence_splitter import split_sentences

TH_RULES = get_profile("th").sentence_rules


def test_a_thai_paragraph_splits_on_its_spaces():
    text = "วันนี้อากาศดีมาก ผมชอบกินข้าว เขาไปโรงเรียน"
    assert split_sentences(text, rules=TH_RULES) == ["วันนี้อากาศดีมาก", "ผมชอบกินข้าว", "เขาไปโรงเรียน"]


def test_terminators_still_apply_under_the_whitespace_mode():
    assert split_sentences("ไปไหม? ไปสิ", rules=TH_RULES) == ["ไปไหม?", "ไปสิ"]


def test_a_whitespace_run_inside_brackets_does_not_split():
    text = "เขาพูดว่า (ไปกัน เถอะ) แล้วก็ไป"
    assert split_sentences(text, rules=TH_RULES) == ["เขาพูดว่า", "(ไปกัน เถอะ)", "แล้วก็ไป"]


def test_a_run_of_several_spaces_and_newlines_is_one_boundary():
    assert split_sentences("ก ข", rules=TH_RULES) == ["ก", "ข"]
    assert split_sentences("ก   \n  ข", rules=TH_RULES) == ["ก", "ข"]


def test_an_unspaced_paragraph_returns_as_one_sentence():
    text = "วันนี้อากาศดีมากผมชอบกินข้าว"
    assert split_sentences(text, rules=TH_RULES) == [text]


def test_the_flag_defaults_off_and_every_other_language_is_unchanged():
    assert (
        SentenceRules(
            terminators=frozenset("."), ellipses=frozenset(), openers=frozenset(), closers=frozenset()
        ).split_on_whitespace
        is False
    )
    # ja: rules=None is the module constants, verbatim.
    assert split_sentences("今日は晴れです。散歩に行きます。") == ["今日は晴れです。", "散歩に行きます。"]
    ko = get_profile("ko").sentence_rules
    assert ko.split_on_whitespace is False
    assert split_sentences("저는 학생입니다. 밥을 먹어요.", rules=ko) == ["저는 학생입니다.", "밥을 먹어요."]

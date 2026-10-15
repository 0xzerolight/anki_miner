"""Sentence choice in WordFilterService: the caps rule, F-06 reassignment, F-07 ranking."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import bound_mined_form, get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.services.word_filter import WordFilterService, within_sentence_caps


def _ja(front: str, sentence: str, start: float) -> TokenizedWord:
    """A Japanese noun: its card front is its surface."""
    return TokenizedWord(
        surface=front,
        lemma=front,
        reading="ヨミ",
        sentence=sentence,
        start_time=start,
        end_time=start + 2.0,
        duration=2.0,
        pos="名詞",
    )


def _other(front: str, sentence: str, start: float) -> TokenizedWord:
    """A word of a non-ja language: its card front is set by its profile's policy (pinned here)."""
    return TokenizedWord(
        surface=front,
        lemma=front,
        reading="",
        sentence=sentence,
        start_time=start,
        end_time=start + 2.0,
        duration=2.0,
        pos="NOUN",
        mined_form_override=front,
    )


def _line(text: str, start: float, *fronts: str, duration: float = 2.0) -> LineLemmas:
    """A parsed line carrying ``fronts`` (each front is its own surface on the line)."""
    spans = tuple((front, front, text.index(front), text.index(front) + len(front), -1) for front in fronts)
    return LineLemmas(
        text,
        frozenset(fronts),
        start,
        start + duration,
        duration,
        front_spans=spans,
        fronts=frozenset(fronts),
    )


def _ja_filter() -> WordFilterService:
    return WordFilterService(replace(AnkiMinerConfig(), bold_target_in_sentence=False))


def _profile_filter(code: str) -> WordFilterService:
    """A WordFilterService wired from ``code``'s profile the way service_factory wires it."""
    config = replace(switch_language(AnkiMinerConfig(), code), bold_target_in_sentence=False)
    profile = get_profile(code)
    return WordFilterService(
        config,
        mined_form=bound_mined_form(profile, config),
        script=profile.script,
        dedup_fold=profile.dedup_fold,
        expression_tracks_surface=getattr(profile.mined_form, "expression_tracks_surface", None),
        sentence_annotation=profile.sentence_annotator is not None,
    )


@pytest.mark.parametrize(
    ("duration", "text", "max_duration", "max_chars", "expected"),
    [
        (9.0, "x" * 500, 0.0, 0, True),
        (5.0, "abc", 5.0, 3, True),
        (5.1, "abc", 5.0, 0, False),
        (1.0, "abcd", 0.0, 3, False),
    ],
)
def test_within_sentence_caps(duration, text, max_duration, max_chars, expected):
    assert within_sentence_caps(duration, text, max_duration, max_chars) is expected


def test_the_sentence_length_filter_applies_the_same_rule():
    service = _ja_filter()
    short = _ja("猫", "猫だ。", 0.0)
    long = _ja("犬", "犬がとても長い文に出てくる。", 5.0)

    kept = service.filter_by_sentence_length([short, long], max_chars=5)

    assert kept == [short]


def test_candidates_still_list_every_compatible_line_in_order():
    service = _ja_filter()
    word = _ja("犬", "犬が走る。", 10.0)
    index = [
        _line("猫がいる。", 0.0, "猫"),
        _line("犬が走る。", 10.0, "犬"),
        _line("犬と猫。", 20.0, "犬", "猫"),
    ]

    service.attach_sentence_candidates([word], index)

    assert [candidate.sentence for candidate in word.sentence_candidates] == ["犬が走る。", "犬と猫。"]


# --- F-06: one card per sentence moves a losing word ------------------------


def _three_line_episode():
    """猫 and 犬 first appear together on A; 犬 also has B and C, 鳥 owns B by first appearance."""
    line_a = _line("猫と犬がいる。", 0.0, "猫", "犬")
    line_b = _line("犬と鳥が遊ぶ。", 10.0, "犬", "鳥")
    line_c = _line("犬が走る。", 20.0, "犬")
    words = [
        _ja("猫", line_a.line_text, 0.0),
        _ja("犬", line_a.line_text, 0.0),
        _ja("鳥", line_b.line_text, 10.0),
    ]
    return words, [line_a, line_b, line_c]


def test_a_losing_word_moves_to_its_first_unused_line():
    service = _ja_filter()
    line_a = _line("猫と犬がいる。", 0.0, "猫", "犬")
    line_b = _line("犬が走る。", 10.0, "犬")
    cat, dog = _ja("猫", line_a.line_text, 0.0), _ja("犬", line_a.line_text, 0.0)

    kept = service.deduplicate_by_sentence([cat, dog], line_index=[line_a, line_b])

    assert [word.mined_form for word in kept] == ["猫", "犬"]
    assert kept[0] is cat
    assert kept[1].sentence == "犬が走る。"
    assert kept[1].start_time == pytest.approx(10.0)
    assert kept[1].end_time == pytest.approx(12.0)


def test_a_word_whose_every_line_is_taken_is_still_dropped():
    service = _ja_filter()
    line_a = _line("猫と犬がいる。", 0.0, "猫", "犬")
    cat, dog = _ja("猫", line_a.line_text, 0.0), _ja("犬", line_a.line_text, 0.0)

    kept = service.deduplicate_by_sentence([cat, dog], line_index=[line_a])

    assert kept == [cat]


def test_a_loser_never_takes_the_line_a_later_word_first_appears_on():
    """Two passes: 鳥 keeps B (its first line), so 犬 goes on to C, not B."""
    service = _ja_filter()
    words, index = _three_line_episode()

    kept = service.deduplicate_by_sentence(words, line_index=index)

    assert [(word.mined_form, word.sentence) for word in kept] == [
        ("猫", "猫と犬がいる。"),
        ("犬", "犬が走る。"),
        ("鳥", "犬と鳥が遊ぶ。"),
    ]


def test_every_word_plain_dedup_keeps_stays_on_its_line():
    service = _ja_filter()
    words, index = _three_line_episode()
    plain = service.deduplicate_by_sentence(words)

    kept = service.deduplicate_by_sentence(words, line_index=index)

    assert all(any(word is survivor for word in kept) for survivor in plain)


def test_reassignment_skips_lines_over_the_caps():
    service = _ja_filter()
    line_a = _line("猫と犬がいる。", 0.0, "猫", "犬")
    line_b = _line("犬が走る。", 10.0, "犬", duration=9.0)
    line_c = _line("犬だ。", 30.0, "犬")
    cat, dog = _ja("猫", line_a.line_text, 0.0), _ja("犬", line_a.line_text, 0.0)

    kept = service.deduplicate_by_sentence([cat, dog], line_index=[line_a, line_b, line_c], max_duration=5.0)

    assert kept[1].sentence == "犬だ。"


def test_without_a_line_index_the_first_word_wins_as_before():
    service = _ja_filter()
    line_a = _line("猫と犬がいる。", 0.0, "猫", "犬")
    cat, dog = _ja("猫", line_a.line_text, 0.0), _ja("犬", line_a.line_text, 0.0)

    assert service.deduplicate_by_sentence([cat, dog]) == [cat]


def test_the_cue_merge_dedup_never_reassigns():
    """``text_of`` (the automatic cue merge's merged text) keeps first-wins (L3a ruling 6)."""
    service = _ja_filter()
    line_a = _line("猫と犬がいる。", 0.0, "猫", "犬")
    line_b = _line("犬が走る。", 10.0, "犬")
    cat, dog = _ja("猫", line_a.line_text, 0.0), _ja("犬", line_a.line_text, 0.0)

    kept = service.deduplicate_by_sentence([cat, dog], lambda word: word.sentence, line_index=[line_a, line_b])

    assert kept == [cat]


def test_es_losing_word_moves_to_another_line():
    """Review Focus 3: a spaced, lemma-fronted language through the same reassignment."""
    service = _profile_filter("es")
    line_a = _line("el perro y el gato", 0.0, "perro", "gato")
    line_b = _line("un gato negro", 5.0, "gato")
    dog, cat = _other("perro", line_a.line_text, 0.0), _other("gato", line_a.line_text, 0.0)

    kept = service.deduplicate_by_sentence([dog, cat], line_index=[line_a, line_b])

    assert [(word.mined_form, word.sentence) for word in kept] == [
        ("perro", "el perro y el gato"),
        ("gato", "un gato negro"),
    ]

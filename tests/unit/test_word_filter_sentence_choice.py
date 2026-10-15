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

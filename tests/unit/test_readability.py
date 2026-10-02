"""Readability against known words (Utilities → Readability, #132).

``measure`` scores one parsed file on the mining Phase-2 basis; ``combine``
totals a run. ``make_tokenized_word`` leaves ``pos=None``, so a word's
``mined_form`` is its surface.
"""

from __future__ import annotations

from collections import Counter

import pytest

from anki_miner.models import LineLemmas
from anki_miner.models.readability import ReadabilityStats
from anki_miner.services.readability import combine, measure
from anki_miner.services.word_filter import WordFilterService


def _line(*lemmas: str) -> LineLemmas:
    return LineLemmas(line_text="".join(lemmas), lemmas=frozenset(lemmas), start_time=0.0, end_time=1.0, duration=1.0)


def _w(make, form: str, lemma: str | None = None):
    return make(surface=form, lemma=lemma or form)


def test_known_share_counts_occurrences(test_config, make_tokenized_word):
    words = [_w(make_tokenized_word, "猫"), _w(make_tokenized_word, "犬")]
    stats = measure(
        words,
        [_line("猫"), _line("猫", "犬")],
        Counter({"猫": 3, "犬": 1}),
        {"猫"},
        WordFilterService(test_config),
    )
    assert (stats.word_count, stats.unknown_count) == (4, 1)
    assert stats.known_pct == pytest.approx(75.0)
    assert stats.new_words == frozenset({"犬"})


def test_lines_bucket_by_unknown_lemmas_capped_at_two(test_config, make_tokenized_word):
    words = [_w(make_tokenized_word, form) for form in ("猫", "犬", "鳥", "魚")]
    lines = [_line("猫"), _line("猫", "犬"), _line("犬", "鳥"), _line("犬", "鳥", "魚")]
    stats = measure(words, lines, Counter(dict.fromkeys("猫犬鳥魚", 1)), {"猫"}, WordFilterService(test_config))
    assert stats.line_buckets == (1, 1, 2)
    assert [stats.line_pct(i) for i in range(3)] == [25.0, 25.0, 50.0]


def test_kana_spelling_of_a_known_lemma_is_known(test_config, make_tokenized_word):
    stats = measure(
        [_w(make_tokenized_word, "うなずく", "頷く")],
        [_line("頷く")],
        Counter({"頷く": 2}),
        {"頷く"},
        WordFilterService(test_config),
    )
    assert stats.unknown_count == 0
    assert stats.line_buckets == (1, 0, 0)


def test_empty_file_has_no_percentages(test_config):
    stats = measure([], [], Counter(), set(), WordFilterService(test_config))
    assert stats.known_pct is None
    assert stats.line_pct(1) is None
    assert stats.line_count == 0


def test_combine_is_occurrence_weighted_and_unions_new_words():
    long_file = ReadabilityStats(100, 10, frozenset({"犬"}), (8, 1, 1))
    short_file = ReadabilityStats(10, 10, frozenset({"犬", "鳥"}), (0, 0, 2))
    total = combine([long_file, short_file])
    assert total.known_pct == pytest.approx(90 / 110 * 100)  # not mean(90, 0)
    assert total.new_words == frozenset({"犬", "鳥"})
    assert total.line_buckets == (8, 1, 3)


def test_combine_of_nothing_has_no_percentages():
    assert combine([]).known_pct is None

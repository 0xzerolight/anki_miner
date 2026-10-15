"""Sentence choice in WordFilterService: the caps rule, F-06 reassignment, F-07 ranking."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import bound_mined_form, get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.services.word_filter import (
    WordFilterService,
    best_sentence_key,
    sentence_length,
    within_sentence_caps,
)


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


# --- F-07: best-sentence pick -------------------------------------------------

_DOG_ONLY = {"犬"}
_CROWD = {"犬", "鳥", "魚"}


def test_sentence_length_counts_words_where_the_script_spaces_them():
    assert sentence_length("el perro come pan", spaced=True) == 4


def test_sentence_length_counts_characters_where_it_does_not():
    assert sentence_length("我 喜欢 猫", spaced=False) == 4
    assert sentence_length("犬がいる。", spaced=False) == 5


def test_the_line_with_fewest_unknowns_wins():
    service = _ja_filter()
    crowded = _line("犬と鳥と魚がいる。", 0.0, "犬", "鳥", "魚")
    clear = _line("犬がいる。", 10.0, "犬")
    dog = _ja("犬", crowded.line_text, 0.0)

    (picked,) = service.pick_best_sentences([dog], [crowded, clear], _CROWD, _CROWD, spaced=False)

    assert picked.sentence == "犬がいる。"
    assert picked.start_time == pytest.approx(10.0)
    assert picked.end_time == pytest.approx(12.0)


def test_a_line_inside_the_band_beats_a_shorter_one_outside_it():
    service = _ja_filter()
    terse = _line("犬だ。", 0.0, "犬")  # 3 characters: under the 10-character floor
    full = _line("公園で大きな犬が走っている。", 10.0, "犬")  # 14 characters
    dog = _ja("犬", terse.line_text, 0.0)

    (picked,) = service.pick_best_sentences([dog], [terse, full], _DOG_ONLY, _DOG_ONLY, spaced=False)

    assert picked.sentence == full.line_text


def test_the_shorter_line_wins_inside_the_band():
    service = _ja_filter()
    long = _line("昨日の夕方に公園で大きな犬を見かけました。", 0.0, "犬")  # 21 characters
    short = _line("公園で大きな犬が走っている。", 10.0, "犬")  # 14 characters
    dog = _ja("犬", long.line_text, 0.0)

    (picked,) = service.pick_best_sentences([dog], [long, short], _DOG_ONLY, _DOG_ONLY, spaced=False)

    assert picked.sentence == short.line_text


def test_the_earlier_line_breaks_a_tie_and_an_unmoved_word_is_the_same_object():
    service = _ja_filter()
    first = _line("犬がいる。", 0.0, "犬")
    second = _line("犬がいた。", 10.0, "犬")
    dog = _ja("犬", first.line_text, 0.0)

    (picked,) = service.pick_best_sentences([dog], [first, second], _DOG_ONLY, _DOG_ONLY, spaced=False)

    assert picked is dog


def test_a_line_over_the_caps_is_never_picked():
    service = _ja_filter()
    crowded = _line("犬と鳥と魚がいる。", 0.0, "犬", "鳥", "魚")
    clear = _line("犬がいる。", 10.0, "犬", duration=9.0)
    dog = _ja("犬", crowded.line_text, 0.0)

    (picked,) = service.pick_best_sentences([dog], [crowded, clear], _CROWD, _CROWD, spaced=False, max_duration=5.0)

    assert picked is dog


def test_a_word_whose_every_line_breaks_the_caps_keeps_its_own():
    service = _ja_filter()
    crowded = _line("犬と鳥と魚がいる。", 0.0, "犬", "鳥", "魚", duration=9.0)
    clear = _line("犬がいる。", 10.0, "犬", duration=9.0)
    dog = _ja("犬", crowded.line_text, 0.0)

    (picked,) = service.pick_best_sentences([dog], [crowded, clear], _CROWD, _CROWD, spaced=False, max_duration=5.0)

    assert picked is dog


def test_the_words_themselves_count_as_unknown():
    """The targets are unioned into the snapshot, as filter_i_plus_one does."""
    service = _ja_filter()
    crowded = _line("犬と鳥がいる。", 0.0, "犬", "鳥")
    clear = _line("犬が走る。", 10.0, "犬")
    dog, bird = _ja("犬", crowded.line_text, 0.0), _ja("鳥", crowded.line_text, 0.0)

    picked = service.pick_best_sentences([dog, bird], [crowded, clear], set(), set(), spaced=False)

    assert [word.sentence for word in picked] == ["犬が走る。", crowded.line_text]
    assert picked[1] is bird


def test_es_ranks_by_words_not_characters():
    """Review Focus 3: a spaced script measures words (3 words is under the 4-word floor)."""
    service = _profile_filter("es")
    terse = _line("el perro come", 0.0, "perro")  # 3 words, 11 characters
    long = _line(
        "mi perro grande y negro duerme tranquilamente todo el día en el sofá viejo", 10.0, "perro"
    )  # 14 words, 61 characters
    dog = _other("perro", terse.line_text, 0.0)

    (by_words,) = service.pick_best_sentences([dog], [terse, long], {"perro"}, {"perro"}, spaced=True)
    (by_characters,) = service.pick_best_sentences([dog], [terse, long], {"perro"}, {"perro"}, spaced=False)

    assert by_words.sentence == long.line_text
    assert by_characters is dog


def test_zh_ranks_by_characters_not_words():
    """Review Focus 3, segmentation: a zh line is one whitespace 'word', so only characters tell lines apart."""
    service = _profile_filter("zh")
    terse = _line("我的狗", 0.0, "狗")  # 3 characters
    full = _line("我昨天在公园里看见了一只很大的狗", 10.0, "狗")  # 16 characters
    dog = _other("狗", terse.line_text, 0.0)

    (by_characters,) = service.pick_best_sentences([dog], [terse, full], {"狗"}, {"狗"}, spaced=False)
    (by_words,) = service.pick_best_sentences([dog], [terse, full], {"狗"}, {"狗"}, spaced=True)

    assert by_characters.sentence == full.line_text
    assert by_words is dog


# --- C6: F-07's key for a word already on a line (the season pool) ------------


def _counted(word: TokenizedWord, unknown: int) -> TokenizedWord:
    """``word`` as the season pre-pass leaves it: ``line_unknown_count`` stamped."""
    return replace(word, line_unknown_count=unknown)


def test_the_season_key_ranks_unknowns_then_band_then_length():
    key = best_sentence_key(spaced=False)
    many = _counted(_ja("犬", "犬と猫と鳥がいる部屋だよ", 1.0), 3)  # 12 characters
    short = _counted(_ja("犬", "犬だ", 5.0), 1)  # 2 characters, below the band
    few = _counted(_ja("犬", "犬がいる静かな部屋だよ", 9.0), 1)  # 11 characters
    assert [word.sentence for word in sorted([many, short, few], key=key)] == [
        "犬がいる静かな部屋だよ",
        "犬だ",
        "犬と猫と鳥がいる部屋だよ",
    ]


def test_an_uncounted_line_sorts_after_every_counted_one():
    key = best_sentence_key(spaced=False)
    uncounted = _counted(_ja("犬", "犬がいる静かな部屋だよ", 1.0), 0)
    crowded = _counted(_ja("犬", "犬がいる静かな部屋だよ", 5.0), 5)
    assert key(crowded) < key(uncounted)


def test_a_line_over_the_caps_sorts_last_and_all_such_lines_tie():
    key = best_sentence_key(spaced=False, max_chars=8)
    inside = _counted(_ja("犬", "犬と猫と鳥だ", 1.0), 3)  # 6 characters
    over = _counted(_ja("犬", "犬がいる静かな部屋だよ", 5.0), 1)  # 11 characters
    also_over = _counted(_ja("犬", "犬と猫と鳥がいる部屋だよ", 9.0), 1)  # 12 characters
    assert key(inside) < key(over)
    assert key(over) == key(also_over)
    duration_key = best_sentence_key(spaced=False, max_duration=3.0)
    assert duration_key(replace(inside, duration=4.0)) > duration_key(over)


def test_es_season_key_counts_words_where_the_script_spaces_them():
    terse = _counted(_other("perro", "Mi perro come", 1.0), 1)  # 3 words, 11 characters
    full = _counted(_other("perro", "El perro duerme en la casa grande", 5.0), 1)  # 7 words
    assert min([terse, full], key=best_sentence_key(spaced=True)) is full
    assert min([terse, full], key=best_sentence_key(spaced=False)) is terse

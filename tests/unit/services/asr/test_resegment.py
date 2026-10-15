"""MS-21: long Whisper segments re-broken at the mining language's sentence ends (services/asr/resegment.py)."""

from __future__ import annotations

from types import SimpleNamespace

from anki_miner.languages.registry import get_profile
from anki_miner.services.asr.resegment import split_segment


def _words(*spec: tuple[str, float, float]) -> list[SimpleNamespace]:
    return [SimpleNamespace(word=w, start=s, end=e) for w, s, e in spec]


def test_a_long_japanese_segment_splits_after_each_full_stop():
    rules = get_profile("ja").sentence_rules
    words = _words(
        ("今日", 0.0, 0.6),
        ("は", 0.6, 0.8),
        ("晴れ", 0.8, 1.4),
        ("です。", 1.4, 2.5),
        ("明日", 3.0, 3.6),
        ("は", 3.6, 3.8),
        ("雨", 3.8, 4.4),
        ("でしょう。", 4.4, 5.6),
        ("傘", 6.0, 6.5),
        ("を", 6.5, 6.7),
        ("持って", 6.7, 7.4),
        ("ね。", 7.4, 8.0),
    )

    pieces = split_segment(0.0, 8.0, "今日は晴れです。明日は雨でしょう。傘を持ってね。", words, rules)

    assert pieces == [
        (0.0, 2.5, "今日は晴れです。"),
        (3.0, 5.6, "明日は雨でしょう。"),
        (6.0, 8.0, "傘を持ってね。"),
    ]


def test_a_closing_bracket_after_the_full_stop_still_ends_the_sentence():
    rules = get_profile("ja").sentence_rules
    words = _words(("「行こう。」", 0.0, 3.0), ("彼", 3.5, 4.0), ("は", 4.0, 4.2), ("言った。", 4.2, 7.0))

    pieces = split_segment(0.0, 7.0, "「行こう。」彼は言った。", words, rules)

    assert [p[2] for p in pieces] == ["「行こう。」", "彼は言った。"]


def test_a_short_segment_stays_whole():
    rules = get_profile("ja").sentence_rules
    words = _words(("はい。", 0.0, 1.0), ("そう", 1.5, 2.0), ("です。", 2.0, 3.0))

    assert split_segment(0.0, 3.0, " はい。そうです。 ", words, rules) == [(0.0, 3.0, "はい。そうです。")]


def test_a_piece_under_a_second_joins_its_neighbour():
    rules = get_profile("ja").sentence_rules
    words = _words(("はい。", 0.0, 0.4), ("それ", 0.6, 1.5), ("で", 1.5, 2.0), ("いい", 2.0, 4.0), ("です。", 4.0, 7.0))

    pieces = split_segment(0.0, 7.0, "はい。それでいいです。", words, rules)

    assert pieces == [(0.0, 7.0, "はい。それでいいです。")]


def test_no_words_or_no_rules_keeps_the_segment():
    rules = get_profile("ja").sentence_rules
    assert split_segment(0.0, 9.0, "長い。文。", None, rules) == [(0.0, 9.0, "長い。文。")]
    words = _words(("長い。", 0.0, 4.0), ("文。", 4.0, 9.0))
    assert split_segment(0.0, 9.0, "長い。文。", words, None) == [(0.0, 9.0, "長い。文。")]


def test_spanish_splits_at_full_stops_but_not_after_an_abbreviation():
    rules = get_profile("es").sentence_rules
    words = _words(
        (" Habló", 0.0, 0.5),
        (" con", 0.5, 0.7),
        (" el", 0.7, 0.9),
        (" Sr.", 0.9, 1.4),
        (" García", 1.4, 2.0),
        (" ayer.", 2.0, 3.0),
        (" Hoy", 3.5, 4.0),
        (" llueve", 4.0, 4.8),
        (" mucho.", 4.8, 7.0),
    )

    pieces = split_segment(0.0, 7.0, " Habló con el Sr. García ayer. Hoy llueve mucho.", words, rules)

    assert pieces == [(0.0, 3.0, "Habló con el Sr. García ayer."), (3.5, 7.0, "Hoy llueve mucho.")]


def test_chinese_splits_at_punctuation_only():
    rules = get_profile("zh").sentence_rules
    words = _words(
        ("我们", 0.0, 0.8),
        ("走", 0.8, 1.2),
        ("吧", 1.2, 1.6),
        ("！", 1.6, 2.0),
        ("今天", 2.5, 3.2),
        ("天气", 3.2, 4.0),
        ("很", 4.0, 4.4),
        ("好", 4.4, 5.2),
        ("，", 5.2, 5.4),
        ("对", 5.6, 6.2),
        ("吧", 6.2, 6.6),
        ("？", 6.6, 7.0),
    )

    pieces = split_segment(0.0, 7.0, "我们走吧！今天天气很好，对吧？", words, rules)

    assert pieces == [(0.0, 2.0, "我们走吧！"), (2.5, 7.0, "今天天气很好，对吧？")]

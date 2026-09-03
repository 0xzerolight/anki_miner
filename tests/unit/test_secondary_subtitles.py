"""Tests for the secondary-language cue join."""

from __future__ import annotations

from anki_miner.models import TokenizedWord
from anki_miner.services.secondary_subtitles import attach_translations, match_secondary_line

ENTRIES = [(0.0, 2.0, "Hello."), (2.0, 4.0, "How are you?"), (3.9, 6.0, "Fine.")]


def _word(start: float, end: float) -> TokenizedWord:
    return TokenizedWord(
        surface="猫", lemma="猫", reading="ネコ", sentence="猫だ", start_time=start, end_time=end, duration=end - start
    )


def test_joins_every_overlapping_cue_in_time_order():
    assert match_secondary_line(ENTRIES, 0.5, 3.5) == "Hello. How are you?"


def test_an_edge_touch_does_not_attach():
    # 2.0-2.05 overlaps the second cue by 0.05 s: neighbouring lines touch like this.
    assert match_secondary_line(ENTRIES, 0.5, 2.05) == "Hello."


def test_no_entries_or_no_overlap_gives_empty():
    assert match_secondary_line([], 0.0, 2.0) == ""
    assert match_secondary_line(ENTRIES, 10.0, 12.0) == ""


def test_offset_shifts_the_secondary_track_onto_the_video_timeline():
    # +1.0 s: "Hello." runs 1.0-3.0 and is the only cue under 0.5-2.5.
    assert match_secondary_line(ENTRIES, 0.5, 2.5, offset=1.0) == "Hello."


def test_a_window_shorter_than_the_threshold_still_matches():
    # Window 0.5 s long, threshold asks for 1.0 s: capped at the window's own length.
    assert match_secondary_line(ENTRIES, 1.0, 1.5, min_overlap=1.0) == "Hello."


def test_out_of_order_entries_join_in_time_order():
    entries = [(2.0, 4.0, "second"), (0.0, 2.0, "first")]
    assert match_secondary_line(entries, 0.5, 3.5) == "first second"


def test_attach_translations_overwrites_from_each_word_s_own_window():
    word = _word(2.0, 4.0)
    attach_translations([word], ENTRIES)
    assert word.sentence_translation == "How are you?"
    word.start_time, word.end_time = 0.5, 3.5  # the window moved (a line expansion)
    attach_translations([word], ENTRIES)
    assert word.sentence_translation == "Hello. How are you?"


def test_attach_translations_applies_the_offset():
    word = _word(0.5, 2.5)
    attach_translations([word], ENTRIES, offset=1.0)
    assert word.sentence_translation == "Hello."

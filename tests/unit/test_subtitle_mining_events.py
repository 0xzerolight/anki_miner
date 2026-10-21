"""One event pass for every reader: karaoke bursts (MS-12), songs, signs and drawings (F-08, SK-19), rolling captions."""

from __future__ import annotations

import pysubs2

from anki_miner.services.subtitle_cleaner import (
    clamp_rolling_ends,
    collapse_frame_bursts,
    drop_non_dialogue_events,
    mining_events,
)


def _ev(start: int, end: int, text: str, style: str = "Default", *, comment: bool = False) -> pysubs2.SSAEvent:
    return pysubs2.SSAEvent(start=start, end=end, text=text, style=style, type="Comment" if comment else "Dialogue")


def _spans(events: list[pysubs2.SSAEvent]) -> list[tuple[int, int, str, str]]:
    return [(event.start, event.end, event.plaintext, event.style) for event in events]


class TestCollapseFrameBursts:
    def test_karaoke_frames_become_one_cue_spanning_the_run(self):
        frames = [_ev(1000 + 40 * i, 1040 + 40 * i, rf"{{\k{i}}}Sing {{\k4}}along", "OP") for i in range(10)]

        out = collapse_frame_bursts(frames)

        assert _spans(out) == [(1000, 1400, "Sing along", "OP")]
        assert out[0].text == frames[0].text

    def test_a_timing_only_burst_needs_three_short_frames(self):
        three = [_ev(0, 200, "Hey"), _ev(200, 400, "Hey"), _ev(400, 600, "Hey")]

        assert _spans(collapse_frame_bursts(three)) == [(0, 600, "Hey", "Default")]

    def test_two_equal_dialogue_lines_in_a_row_stay_two(self):
        lines = [_ev(0, 1500, "No."), _ev(1500, 3000, "No.")]

        assert _spans(collapse_frame_bursts(lines)) == [(0, 1500, "No.", "Default"), (1500, 3000, "No.", "Default")]

    def test_layered_copies_of_one_cue_become_one(self):
        layers = [_ev(5000, 7000, r"{\bord4}SHOP", "Sign-shadow"), _ev(5000, 7000, "SHOP", "Sign")]

        assert _spans(collapse_frame_bursts(layers)) == [(5000, 7000, "SHOP", "Sign-shadow")]

    def test_equal_text_far_apart_is_not_a_burst(self):
        lines = [_ev(0, 100, r"{\k10}La"), _ev(5000, 5100, r"{\k10}La")]

        assert len(collapse_frame_bursts(lines)) == 2

    def test_other_lines_keep_their_place_and_order(self):
        events = [
            _ev(0, 1000, "Before"),
            _ev(1000, 1040, r"{\k1}Ka", "OP"),
            _ev(1000, 2000, "Dialogue during the OP"),
            _ev(1040, 1080, r"{\k2}Ka", "OP"),
            _ev(1080, 1120, r"{\k3}Ka", "OP"),
            _ev(3000, 4000, "After"),
        ]

        assert _spans(collapse_frame_bursts(events)) == [
            (0, 1000, "Before", "Default"),
            (1000, 1120, "Ka", "OP"),
            (1000, 2000, "Dialogue during the OP", "Default"),
            (3000, 4000, "After", "Default"),
        ]

    def test_the_input_events_are_not_changed(self):
        frames = [_ev(0, 40, r"{\k1}Ka"), _ev(40, 80, r"{\k2}Ka")]

        collapse_frame_bursts(frames)

        assert [(event.start, event.end) for event in frames] == [(0, 40), (40, 80)]

    def test_events_without_visible_text_never_group(self):
        blanks = [_ev(0, 40, ""), _ev(40, 80, ""), _ev(80, 120, "")]

        assert len(collapse_frame_bursts(blanks)) == 3


class TestDropNonDialogueEvents:
    def test_song_and_sign_styles_go_and_dialogue_stays(self):
        events = [
            _ev(0, 1000, "Hello", "Default"),
            _ev(1000, 2000, "Lyric", "OP"),
            _ev(2000, 3000, "How are you", "Default"),
            _ev(3000, 4000, "SHOP", "Signs"),
            _ev(4000, 5000, "Fine", "Default"),
            _ev(5000, 6000, "Lyric two", "ED - Romaji"),
        ]

        assert [event.plaintext for event in drop_non_dialogue_events(events)] == ["Hello", "How are you", "Fine"]

    def test_the_files_main_style_is_kept_whatever_its_name(self):
        events = [_ev(i * 1000, i * 1000 + 900, f"Line {i}", "Caption") for i in range(3)]
        events.append(_ev(9000, 9500, "Lyric", "OP"))

        assert [event.plaintext for event in drop_non_dialogue_events(events)] == ["Line 0", "Line 1", "Line 2"]

    def test_a_positioned_line_outside_the_main_style_is_a_sign(self):
        events = [
            _ev(0, 1000, "Hello"),
            _ev(1000, 2000, r"{\pos(320,50)}Hello up here"),
            _ev(2000, 3000, "Bye"),
            _ev(2000, 3000, r"{\an7\pos(10,10)}Ramen Shop", "TS"),
            _ev(3000, 4000, r"{\move(0,0,100,100)}Train", "Overlay"),
            _ev(4000, 5000, r"Top line{\pos(1,1)}", "Top"),
        ]

        assert [event.plaintext for event in drop_non_dialogue_events(events)] == [
            "Hello",
            "Hello up here",
            "Bye",
            "Top line",
        ]

    def test_a_drawing_goes_in_any_style(self):
        events = [_ev(0, 1000, "Hello"), _ev(1000, 2000, r"{\p1}m 0 0 l 100 0 100 100{\p0}"), _ev(2000, 3000, "Bye")]

        assert [event.plaintext for event in drop_non_dialogue_events(events)] == ["Hello", "Bye"]

    def test_an_unstyled_file_is_untouched(self):
        events = [_ev(0, 1000, "One"), _ev(1000, 2000, "Two")]

        assert drop_non_dialogue_events(events) == events

    def test_no_events(self):
        assert drop_non_dialogue_events([]) == []


#: The first cues of a YouTube auto-caption track (#137): each line stays on
#: screen until the line after next appears.
_ROLLING = [(199, 6640), (3959, 8719), (6640, 11200), (8719, 13759), (11200, 15960)]


def _rolling() -> list[pysubs2.SSAEvent]:
    return [_ev(start, end, f"Line {i}") for i, (start, end) in enumerate(_ROLLING)]


class TestClampRollingEnds:
    def test_a_rolling_track_ends_each_cue_at_the_next_start(self):
        out = clamp_rolling_ends(_rolling())

        assert [(event.start, event.end) for event in out] == [
            (199, 3959),
            (3959, 6640),
            (6640, 8719),
            (8719, 11200),
            (11200, 15960),
        ]

    def test_a_track_without_overlaps_is_unchanged(self):
        lines = [_ev(0, 1000, "One"), _ev(1200, 2000, "Two"), _ev(2000, 3000, "Three")]

        assert _spans(clamp_rolling_ends(lines)) == _spans(lines)

    def test_a_few_overlapping_lines_are_left_alone(self):
        """Two speakers talking over each other once is dialogue, not a rolling track."""
        lines = [_ev(0, 1000, "A"), _ev(1500, 3500, "B"), _ev(3000, 4000, "C"), _ev(4500, 5000, "D")]

        assert _spans(clamp_rolling_ends(lines)) == _spans(lines)

    def test_same_start_twins_are_not_rolling(self):
        """A dual-language file shows each line twice from one start; neither clamps the other to nothing."""
        lines = [_ev(0, 2000, "こんにちは"), _ev(0, 2000, "Hello"), _ev(2500, 4000, "またね"), _ev(2500, 4000, "Bye")]

        assert _spans(clamp_rolling_ends(lines)) == _spans(lines)

    def test_cues_sharing_a_start_in_a_rolling_track_end_at_the_next_later_start(self):
        events = _rolling()
        events.insert(1, _ev(199, 6640, "Twin"))

        out = clamp_rolling_ends(events)

        assert [(event.start, event.end) for event in out[:2]] == [(199, 3959), (199, 3959)]

    def test_events_keep_their_order(self):
        events = list(reversed(_rolling()))

        out = clamp_rolling_ends(events)

        assert [event.plaintext for event in out] == [event.plaintext for event in events]
        assert [(event.start, event.end) for event in out][-1] == (199, 3959)

    def test_the_input_events_are_not_changed(self):
        events = _rolling()

        clamp_rolling_ends(events)

        assert [(event.start, event.end) for event in events] == _ROLLING

    def test_no_events(self):
        assert clamp_rolling_ends([]) == []


class TestMiningEvents:
    _DIALOGUE = [_ev(0, 1000, "One"), _ev(1000, 2000, "Two"), _ev(2000, 3000, "Three")]
    # Twenty frames would outnumber the three dialogue lines if styles were
    # counted before the collapse, and the karaoke style would pass as the main one.
    _KARAOKE = [_ev(10_000 + 40 * i, 10_040 + 40 * i, rf"{{\k{i}}}La la", "Karaoke") for i in range(20)]

    def test_switch_on_collapses_then_drops(self):
        out = mining_events(self._DIALOGUE + self._KARAOKE, skip_non_dialogue=True)

        assert [event.plaintext for event in out] == ["One", "Two", "Three"]

    def test_switch_off_keeps_songs_but_still_collapses(self):
        out = mining_events(self._DIALOGUE + self._KARAOKE, skip_non_dialogue=False)

        assert [(event.start, event.end, event.plaintext) for event in out] == [
            (0, 1000, "One"),
            (1000, 2000, "Two"),
            (2000, 3000, "Three"),
            (10_000, 10_800, "La la"),
        ]

    def test_comments_never_come_through(self):
        events = [_ev(0, 1000, "Note to the editor", comment=True), _ev(0, 1000, "Hi")]

        assert [event.plaintext for event in mining_events(events, skip_non_dialogue=False)] == ["Hi"]

    def test_a_rolling_track_reaches_the_readers_clamped(self):
        out = mining_events(_rolling(), skip_non_dialogue=False)

        assert [event.end for event in out] == [3959, 6640, 8719, 11200, 15960]

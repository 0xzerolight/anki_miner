"""One event pass for every reader: karaoke bursts (MS-12), songs, signs and drawings (F-08, SK-19)."""

from __future__ import annotations

import pysubs2

from anki_miner.services.subtitle_cleaner import collapse_frame_bursts


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

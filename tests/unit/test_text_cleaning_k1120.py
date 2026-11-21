"""Text cleaning and cue gating on streaming captions, caption-rip ASS and fansub ASS (session K1120).

Every line here is hand-written in the shape the corpus files have.
"""

from __future__ import annotations

import pysubs2

from anki_miner.services.subtitle_cleaner import mining_events


def _event(text: str, *, start: int = 0, end: int = 2000, style: str = "Default") -> pysubs2.SSAEvent:
    return pysubs2.SSAEvent(start=start, end=end, text=text, style=style)


def _mined_texts(events: list[pysubs2.SSAEvent], *, skip_non_dialogue: bool = True) -> list[str]:
    return [event.plaintext for event in mining_events(events, skip_non_dialogue=skip_non_dialogue)]


class TestRubyReadingEvents:
    """A caption rip writes a ruby reading as its own half-size event above the caption row."""

    def test_reading_row_goes_and_the_caption_row_stays(self):
        events = [
            _event(r"{\pos(272,443)\fscx50\fscy50}ぼく"),
            _event(r"{\pos(272,497)}僕の番だ{\fscx50}。"),
        ]
        assert _mined_texts(events) == ["僕の番だ。"]

    def test_several_reading_rows_over_one_caption_row(self):
        events = [
            _event(r"{\pos(432,443)\fscx50\fscy50}きょうだい"),
            _event(r"{\pos(572,443)\fscx50\fscy50}く"),
            _event(r"{\pos(172,497)}いずれ{\fscx50}　{\fscx100}ほかの兄妹も暮らす"),
        ]
        assert _mined_texts(events) == ["いずれ　ほかの兄妹も暮らす"]

    def test_dropped_without_the_opening_ending_switch(self):
        events = [_event(r"{\pos(272,443)\fscx50\fscy50}ぼく"), _event(r"{\pos(272,497)}僕の番だ")]
        assert _mined_texts(events, skip_non_dialogue=False) == ["僕の番だ"]

    def test_half_width_only_is_speech(self):
        events = [
            _event(r"{\pos(412,497)\fscx50}ターンエンド{\fscx100}です{\fscx50}。"),
            _event(r"{\pos(552,407)\fscx50}フン。", start=3000, end=4000),
        ]
        assert _mined_texts(events) == ["ターンエンドです。", "フン。"]

    def test_two_thirds_size_dialogue_stays(self):
        events = [_event(r"{\pos(960,1050)\fscx67\fscy67}それでは")]
        assert _mined_texts(events) == ["それでは"]

    def test_half_size_start_that_grows_back_is_speech(self):
        events = [_event(r"{\pos(272,497)\fscx50\fscy50}あの{\fscx100\fscy100}人は来ない")]
        assert _mined_texts(events) == ["あの人は来ない"]

    def test_scale_set_after_the_first_block_is_not_a_reading_row(self):
        events = [_event(r"{\pos(272,497)}今日は{\fscx50\fscy50}いい天気")]
        assert _mined_texts(events) == ["今日はいい天気"]

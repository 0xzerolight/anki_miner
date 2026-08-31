"""Tests for the dialogue-only cleaning + delta map-back module."""

from pathlib import Path

import pysubs2
import pytest

from anki_miner.services.subtitle_cleaner import (
    CleanedForAlignment,
    _is_non_speech_text,
    clean_for_alignment,
    clean_reference,
    map_deltas_back,
    transcode_for_alignment,
)


def _dialogue(start: int, end: int, text: str = "せりふ", style: str = "Default") -> pysubs2.SSAEvent:
    return pysubs2.SSAEvent(start=start, end=end, text=text, style=style)


def _save(path: Path, events: list[pysubs2.SSAEvent]) -> Path:
    subs = pysubs2.SSAFile()
    subs.events = events
    subs.save(str(path), encoding="utf-8")
    return path


def _make_dialogue(count: int, start: int = 0, step: int = 2000) -> list[pysubs2.SSAEvent]:
    return [_dialogue(start + i * step, start + i * step + 1000) for i in range(count)]


class TestNonSpeechText:
    @pytest.mark.parametrize(
        "text",
        [
            "♪～",
            "♪♪～",
            "",
            "…",
            "（雷鳴）",
            "(sighs)",
            "（雨の音）（雷鳴）",
            "(かんじ)",
        ],
    )
    def test_non_speech(self, text):
        assert _is_non_speech_text(text)

    @pytest.mark.parametrize(
        "text",
        [
            "こんにちは",
            "こんにちは（笑）",
            "漢字(かんじ)だ",
            "え？",
            "Hello there",
        ],
    )
    def test_speech(self, text):
        assert not _is_non_speech_text(text)


class TestCleanReference:
    def test_drops_music_and_annotation_only_cues(self, tmp_path):
        src = _save(
            tmp_path / "ref.ass",
            [
                _dialogue(0, 1000, "♪～"),
                _dialogue(2000, 3000, "（雷鳴）"),
                _dialogue(4000, 5000, "talk"),
            ],
        )
        dest = tmp_path / "out.srt"
        stats = clean_reference(src, dest)
        assert stats.cues == 1
        assert stats.span_ms == 1000
        out = pysubs2.load(str(dest))
        assert [e.start for e in out.events] == [4000]


class TestCleanForAlignment:
    def test_records_kept_indices_and_preserves_format(self, tmp_path):
        events = _make_dialogue(12)
        events.insert(3, _dialogue(6300, 6400, "♪～"))
        events.insert(7, _dialogue(12500, 12600, "sign", style="Signs"))
        src = _save(tmp_path / "in.ass", events)

        cleaned = clean_for_alignment(src, tmp_path / "in.clean.ass")

        assert isinstance(cleaned, CleanedForAlignment)
        assert cleaned.total_events == 14
        assert cleaned.dropped == 2
        assert 3 not in cleaned.kept_indices
        assert 7 not in cleaned.kept_indices
        assert len(cleaned.kept_indices) == 12
        out = pysubs2.load(str(cleaned.path))
        assert len(out.events) == 12
        # Format preserved: the cleaned copy is still ASS.
        assert cleaned.path.suffix == ".ass"

    def test_too_few_dialogue_cues_returns_none(self, tmp_path):
        src = _save(tmp_path / "in.srt", _make_dialogue(5))
        assert clean_for_alignment(src, tmp_path / "out.srt") is None

    def test_unparsable_returns_none(self, tmp_path):
        src = tmp_path / "in.srt"
        src.write_bytes(b"\x00\x01 not a subtitle")
        assert clean_for_alignment(src, tmp_path / "out.srt") is None


class TestMapDeltasBack:
    def _round_trip(self, tmp_path, events, kept_indices, shift_fn):
        original = _save(tmp_path / "orig.ass", events)
        synced_events = []
        for i in kept_indices:
            e = events[i].copy()
            delta = shift_fn(i)
            e.start += delta
            e.end += delta
            synced_events.append(e)
        synced = _save(tmp_path / "synced.srt", synced_events)
        out = tmp_path / "out.ass"
        assert map_deltas_back(original, synced, kept_indices, out)
        return pysubs2.load(str(out))

    def test_kept_cues_take_exact_new_timings(self, tmp_path):
        events = _make_dialogue(12)
        out = self._round_trip(tmp_path, events, list(range(12)), lambda i: 1500)
        assert [e.start for e in out.events] == [e.start + 1500 for e in events]

    def test_dropped_cue_takes_nearest_anchor_delta(self, tmp_path):
        events = _make_dialogue(12)  # starts 0, 2000, ..., 22000
        dropped = 2  # start 4000: nearest anchors 1 (2000) and 3 (6000)
        kept = [i for i in range(12) if i != dropped]
        # Block boundary between index 1 and 3: first two cues shift +1000,
        # the rest +5000. Nearest anchor to start 4000 is index 3 (6000) vs
        # index 1 (2000) — equidistant, ties to the earlier anchor (+1000).
        out = self._round_trip(tmp_path, events, kept, lambda i: 1000 if i <= 1 else 5000)
        assert out.events[dropped].start == 4000 + 1000

    def test_dropped_cue_outside_anchor_range_clamps_to_edge_anchor(self, tmp_path):
        events = _make_dialogue(12)
        kept = list(range(1, 11))  # first and last dropped
        out = self._round_trip(tmp_path, events, kept, lambda i: 700)
        assert out.events[0].start == events[0].start + 700
        assert out.events[11].start == events[11].start + 700

    def test_negative_result_clamps_to_zero(self, tmp_path):
        events = _make_dialogue(12)
        kept = list(range(1, 12))
        out = self._round_trip(tmp_path, events, kept, lambda i: -2500)
        assert out.events[0].start == 0

    def test_cue_count_mismatch_returns_false(self, tmp_path):
        events = _make_dialogue(12)
        original = _save(tmp_path / "orig.srt", events)
        synced = _save(tmp_path / "synced.srt", _make_dialogue(11))
        assert not map_deltas_back(original, synced, list(range(12)), tmp_path / "out.srt")

    def test_preserves_all_lines_and_styles(self, tmp_path):
        events = _make_dialogue(12)
        events.insert(5, _dialogue(9300, 9400, "sign text", style="Signs"))
        original = _save(tmp_path / "orig.ass", events)
        kept = [i for i in range(13) if i != 5]
        synced_events = [events[i].copy() for i in kept]
        for e in synced_events:
            e.start += 300
            e.end += 300
        synced = _save(tmp_path / "synced.srt", synced_events)
        out_path = tmp_path / "out.ass"
        assert map_deltas_back(original, synced, kept, out_path)
        out = pysubs2.load(str(out_path))
        assert len(out.events) == 13
        assert out.events[5].style == "Signs"
        assert out.events[5].start == 9300 + 300


class TestTranscodeForAlignment:
    """The fallback when clean_for_alignment declines a format alass cannot read."""

    def _vtt(self, path: Path, count: int) -> pysubs2.SSAFile:
        subs = pysubs2.SSAFile()
        for i in range(count):
            start = 1000 + i * 2000
            subs.append(pysubs2.SSAEvent(start=start, end=start + 1500, text=f"テスト{i}"))
        subs.save(str(path), format_="vtt")
        return subs

    def test_transcodes_vtt_to_srt_keeping_every_cue(self, tmp_path):
        src = tmp_path / "in.vtt"
        self._vtt(src, 4)
        dest = tmp_path / "out.srt"

        result = transcode_for_alignment(src, dest)

        assert result is not None
        assert result.kept_indices == [0, 1, 2, 3]
        assert result.dropped == 0
        assert pysubs2.load(str(dest)).format == "srt"

    def test_works_below_the_clean_minimum(self, tmp_path):
        """clean_for_alignment declines under 10 cues; the transcode must not."""
        src = tmp_path / "short.vtt"
        self._vtt(src, 3)

        assert clean_for_alignment(src, tmp_path / "clean.srt") is None
        assert transcode_for_alignment(src, tmp_path / "trans.srt") is not None

    def test_drops_comments_so_the_cue_count_survives_the_writer(self, tmp_path):
        """SRT has no comments and its writer drops them; kept_indices must agree
        or map_deltas_back rejects every candidate on a count mismatch.

        Sourced from .ass because it is the only format here that stores a
        comment at all -- a .vtt cannot carry one into the function.
        """
        subs = pysubs2.SSAFile()
        for i in range(4):
            start = 1000 + i * 2000
            event = pysubs2.SSAEvent(start=start, end=start + 1500, text=f"テスト{i}")
            if i == 1:
                event.type = "Comment"
            subs.append(event)
        src = tmp_path / "commented.ass"
        subs.save(str(src), format_="ass")
        dest = tmp_path / "out.srt"

        result = transcode_for_alignment(src, dest)

        assert result is not None
        assert result.kept_indices == [0, 2, 3]
        assert len(pysubs2.load(str(dest)).events) == len(result.kept_indices)

    def test_drops_non_positive_spans(self, tmp_path):
        """A zero-length cue survives the SRT writer but aligners discard it, so
        it must not be counted as kept."""
        subs = pysubs2.SSAFile()
        subs.append(pysubs2.SSAEvent(start=1000, end=2500, text="いち"))
        subs.append(pysubs2.SSAEvent(start=3000, end=3000, text="ぜろ"))
        subs.append(pysubs2.SSAEvent(start=4000, end=5500, text="さん"))
        src = tmp_path / "zero.vtt"
        subs.save(str(src), format_="vtt")

        result = transcode_for_alignment(src, tmp_path / "out.srt")

        assert result is not None
        assert result.kept_indices == [0, 2]

    def test_unparsable_source_returns_none(self, tmp_path):
        src = tmp_path / "bad.vtt"
        src.write_bytes(b"\x00\x01 not a subtitle \xff")

        assert transcode_for_alignment(src, tmp_path / "out.srt") is None

    def test_round_trips_through_map_deltas_back_into_the_original_format(self, tmp_path):
        """The whole point: timings land on the untouched .vtt, which stays .vtt."""
        src = tmp_path / "in.vtt"
        self._vtt(src, 4)
        transcoded = transcode_for_alignment(src, tmp_path / "align.srt")
        assert transcoded is not None

        synced = pysubs2.load(str(transcoded.path))
        for event in synced:
            event.start += 3000
            event.end += 3000
        synced.save(str(tmp_path / "synced.srt"), format_="srt")

        out = tmp_path / "out.vtt"
        assert map_deltas_back(src, tmp_path / "synced.srt", transcoded.kept_indices, out)

        mapped = pysubs2.load(str(out))
        assert mapped.format == "vtt"
        assert [event.start for event in mapped] == [4000, 6000, 8000, 10000]

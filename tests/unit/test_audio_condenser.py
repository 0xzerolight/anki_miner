"""Unit tests for :mod:`anki_miner.services.audio_condenser`.

Part 1 covers the pure interval-math + subtitle-I/O functions (no ffmpeg, no Qt,
no MeCab; all times integer milliseconds). Part 2 covers ``AudioCondenserService``
ffmpeg orchestration — subprocess is faked (no real ffmpeg is ever invoked; that
is validated later at E2E).
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pysubs2
import pytest

from anki_miner.services.audio_condenser import (
    AudioCondenserService,
    EncoderUnavailableError,
    build_aselect_graph,
    build_periods,
    filter_lines,
    load_subtitle_events,
    map_events_to_condensed,
    shift_events,
    write_condensed_lrc,
    write_condensed_srt,
)
from anki_miner.utils.audio_track_detector import SubtitleStream

# ---------------------------------------------------------------------------
# shift_events
# ---------------------------------------------------------------------------


def test_shift_events_positive_offset():
    events = [(1000, 2000, "a"), (3000, 4000, "b")]
    assert shift_events(events, 500) == [(1500, 2500, "a"), (3500, 4500, "b")]


def test_shift_events_negative_offset_allows_negative_times():
    events = [(200, 1000, "a")]
    assert shift_events(events, -500) == [(-300, 500, "a")]


def test_shift_events_zero_offset_is_identity():
    events = [(0, 100, "x")]
    assert shift_events(events, 0) == events


# ---------------------------------------------------------------------------
# build_periods — merges + padding floor
# ---------------------------------------------------------------------------


def test_build_periods_overlapping_merge():
    events = [(0, 1000, "a"), (500, 1500, "b")]
    assert build_periods(events, 0) == [(0, 1500)]


def test_build_periods_adjacent_merge():
    events = [(0, 1000, "a"), (1000, 2000, "b")]
    assert build_periods(events, 0) == [(0, 2000)]


def test_build_periods_nested_merge():
    events = [(0, 3000, "outer"), (1000, 2000, "inner")]
    assert build_periods(events, 0) == [(0, 3000)]


def test_build_periods_disjoint_kept_separate():
    events = [(0, 1000, "a"), (2000, 3000, "b")]
    assert build_periods(events, 0) == [(0, 1000), (2000, 3000)]


def test_build_periods_unsorted_input_is_sorted():
    events = [(2000, 3000, "b"), (0, 1000, "a")]
    assert build_periods(events, 0) == [(0, 1000), (2000, 3000)]


def test_build_periods_padding_expands_and_merges():
    # Two cues 500ms apart become adjacent once padded by 300 each.
    events = [(0, 1000, "a"), (1500, 2000, "b")]
    assert build_periods(events, 300) == [(0, 2300)]


def test_build_periods_near_zero_cue_floors_at_zero_after_padding():
    # Regression for the pad-after-clamp bug: pad first, THEN floor at 0.
    events = [(100, 500, "a")]
    periods = build_periods(events, 500)
    assert periods == [(0, 1000)]
    assert periods[0][0] == 0  # never negative


def test_build_periods_trailing_pad_not_stripped_from_last_period():
    # D3: unlike the original condenser we keep the trailing pad.
    events = [(1000, 2000, "a")]
    assert build_periods(events, 250) == [(750, 2250)]


def test_build_periods_empty_events():
    assert build_periods([], 500) == []


def test_build_periods_drops_cue_shifted_fully_before_zero():
    # Regression: a cue whose padded end is <= 0 must NOT emit an inverted
    # (0, negative) period, which would drive the condensed offset accumulator
    # negative and map every later cue to negative timestamps.
    raw = [(100, 500, "early"), (3000, 4000, "later")]
    shifted = shift_events(raw, -2000)
    periods = build_periods(shifted, 500)
    # "early" (shifted to -1900..-1500, padded end -1000) is dropped; only the
    # non-inverted "later" period survives.
    assert periods == [(500, 2500)]
    assert all(start < end for start, end in periods)

    out = map_events_to_condensed(shifted, periods)
    # One 1000ms-duration condensed cue for "later"; leading pad becomes silence
    # ahead of it, so it lands at (500, 1500). Crucially: no negative timestamp.
    assert out == [(500, 1500, "later")]
    for start, end, _t in out:
        assert start >= 0
        assert end >= 0
        assert start < end


# ---------------------------------------------------------------------------
# filter_lines
# ---------------------------------------------------------------------------


def test_filter_lines_strips_markup():
    events = [(0, 1000, r"{\pos(1,2)}Hello\Nthere <b>bold</b>")]
    out = filter_lines(events, "")
    assert out == [(0, 1000, "Hello there bold")]


@pytest.mark.parametrize(
    "text",
    ["(aside)", "（独り言）", "[SFX cue]", "{stage note}"],
)
def test_filter_lines_drops_all_four_bracket_pairs(text):
    events = [(0, 1000, text)]
    assert filter_lines(events, "") == []


def test_filter_lines_keeps_partially_bracketed_line():
    events = [(0, 1000, "hello (world)")]
    assert filter_lines(events, "") == [(0, 1000, "hello (world)")]


def test_filter_lines_music_note_only_line_dropped():
    events = [(0, 1000, "♪")]
    assert filter_lines(events, "♪♫♬") == []


def test_filter_lines_removes_filtered_chars_but_keeps_text():
    events = [(0, 1000, "♪♪ lalala ♪♪")]
    assert filter_lines(events, "♪") == [(0, 1000, "lalala")]


def test_filter_lines_drops_line_emptied_by_filtered_chars():
    events = [(0, 1000, "〜〜〜")]
    assert filter_lines(events, "〜") == []


def test_filter_lines_drops_whitespace_only_line():
    events = [(0, 1000, "   \\N  ")]
    assert filter_lines(events, "") == []


def test_filter_lines_keeps_normal_line_with_empty_filter_set():
    events = [(0, 1000, "普通のセリフ")]
    assert filter_lines(events, "") == [(0, 1000, "普通のセリフ")]


# ---------------------------------------------------------------------------
# map_events_to_condensed
# ---------------------------------------------------------------------------


def _total_duration(periods):
    return sum(end - start for start, end in periods)


def test_map_single_period_offsets_to_zero_base():
    periods = [(1000, 2000)]
    events = [(1000, 2000, "a")]
    assert map_events_to_condensed(events, periods) == [(0, 1000, "a")]


def test_map_two_periods_second_shifted_by_first_duration():
    periods = [(0, 1000), (2000, 3000)]
    events = [(2000, 3000, "b")]
    # first period contributes 1000ms of condensed timeline before period 2
    assert map_events_to_condensed(events, periods) == [(1000, 2000, "b")]


def test_map_straddling_cue_clamped_into_period():
    periods = [(1000, 2000)]
    # cue starts before the period: clamp start up to the period start
    assert map_events_to_condensed([(500, 1500, "x")], periods) == [(0, 500, "x")]
    # cue ends after the period: clamp end down to the period end
    assert map_events_to_condensed([(1800, 2500, "y")], periods) == [(800, 1000, "y")]


def test_map_cue_spanning_two_periods_emits_per_intersection():
    periods = [(0, 1000), (2000, 3000)]
    events = [(500, 2500, "span")]
    out = map_events_to_condensed(events, periods)
    assert out == [(500, 1000, "span"), (1000, 1500, "span")]


def test_map_cue_with_empty_intersection_dropped():
    periods = [(1000, 2000)]
    # cue lies entirely in a gap
    assert map_events_to_condensed([(3000, 4000, "gap")], periods) == []


def test_map_no_output_timestamp_negative_and_before_zero_cue_absent():
    # offset<0 pushes one cue across t=0 and another fully before it.
    raw = [(200, 1000, "cross"), (100, 250, "before")]
    shifted = shift_events(raw, -300)
    filtered = filter_lines(shifted, "")
    periods = build_periods(filtered, 50)
    out = map_events_to_condensed(filtered, periods)

    texts = [t for _s, _e, t in out]
    assert "cross" in texts
    assert "before" not in texts  # fully-before-0 cue dropped
    for start, end, _t in out:
        assert start >= 0
        assert end >= 0
        assert start < end


def test_map_offset_nonzero_cues_land_inside_condensed_timeline():
    raw = [(1000, 2000, "a"), (5000, 6000, "b")]
    shifted = shift_events(raw, 500)
    filtered = filter_lines(shifted, "")
    periods = build_periods(filtered, 100)
    out = map_events_to_condensed(filtered, periods)
    total = _total_duration(periods)
    assert out  # nothing dropped
    for start, end, _t in out:
        assert 0 <= start < end <= total


def test_filtered_line_never_appears_in_condensed_output():
    raw = [(0, 1000, "dialogue"), (1000, 2000, "♪")]
    shifted = shift_events(raw, 0)
    filtered = filter_lines(shifted, "♪")
    periods = build_periods(filtered, 0)
    out = map_events_to_condensed(filtered, periods)
    texts = [t for _s, _e, t in out]
    assert "dialogue" in texts
    assert "♪" not in texts


# ---------------------------------------------------------------------------
# load_subtitle_events
# ---------------------------------------------------------------------------


def test_load_subtitle_events_basic_srt(tmp_path):
    subs = pysubs2.SSAFile()
    subs.append(pysubs2.SSAEvent(start=1000, end=2000, text="one"))
    subs.append(pysubs2.SSAEvent(start=3000, end=4000, text="two"))
    path = tmp_path / "basic.srt"
    subs.save(str(path), format_="srt")

    events = load_subtitle_events(path)
    assert events == [(1000, 2000, "one"), (3000, 4000, "two")]


def test_load_subtitle_events_skips_comments(tmp_path):
    subs = pysubs2.SSAFile()
    comment = pysubs2.SSAEvent(start=0, end=1000, text="hidden")
    comment.type = "Comment"
    subs.append(comment)
    subs.append(pysubs2.SSAEvent(start=2000, end=3000, text="shown"))
    path = tmp_path / "with_comment.ass"
    subs.save(str(path), format_="ass")

    events = load_subtitle_events(path)
    assert events == [(2000, 3000, "shown")]


def test_load_subtitle_events_empty_file(tmp_path):
    path = tmp_path / "empty.srt"
    path.write_text("", encoding="utf-8")
    assert load_subtitle_events(path) == []


def test_load_subtitle_events_all_comment_file(tmp_path):
    subs = pysubs2.SSAFile()
    for i in range(3):
        ev = pysubs2.SSAEvent(start=i * 1000, end=i * 1000 + 500, text=f"c{i}")
        ev.type = "Comment"
        subs.append(ev)
    path = tmp_path / "all_comment.ass"
    subs.save(str(path), format_="ass")
    assert load_subtitle_events(path) == []


def test_load_subtitle_events_cp932_fallback(tmp_path):
    # Key regression (D10 amended): a real cp932-encoded SRT — the app's
    # dominant non-UTF-8 input — must decode correctly with NO monkeypatching of
    # the detector. cp932 is now tried before charset-normalizer precisely
    # because the detector confidently mis-detects cp932 Japanese as cp949 and
    # decodes it without error (silent mojibake). This test would produce
    # garbage under the old detector-first order.
    text = "1\r\n" "00:00:01,000 --> 00:00:02,000\r\n" "こんにちは、世界。ありがとうございました。\r\n" "\r\n"
    path = tmp_path / "cp932.srt"
    path.write_bytes(text.encode("cp932"))

    events = load_subtitle_events(path)
    assert events == [(1000, 2000, "こんにちは、世界。ありがとうございました。")]


# This UTF-16 fixture text is chosen so its UTF-16LE bytes contain a cp932 lead
# byte with no valid trail (0x93 mid-string): the cp932 retry raises
# UnicodeDecodeError, so the load deterministically reaches the detector leg.
_UTF16_DETECTOR_TEXT = "1\r\n00:00:01,000 --> 00:00:02,000\r\nこんにちは、世界。ありがとうございました。\r\n\r\n"


def test_load_subtitle_events_detector_leg_utf16(tmp_path):
    # Detector leg (D10 amended): UTF-16-with-BOM fails the UTF-8 default AND the
    # cp932 retry, so the load falls through to charset-normalizer, which detects
    # utf_16. No monkeypatch — this exercises the real detector branch end to end
    # (charset-normalizer detects utf_16 reliably for this fixture).
    path = tmp_path / "utf16.srt"
    path.write_bytes(_UTF16_DETECTOR_TEXT.encode("utf-16"))  # includes a BOM

    assert load_subtitle_events(path) == [(1000, 2000, "こんにちは、世界。ありがとうございました。")]


def test_load_subtitle_events_detector_unavailable_raises_original(tmp_path, monkeypatch):
    # If cp932 also fails to decode and the detector is unavailable (or yields
    # nothing), D10 re-raises the original UTF-8 error rather than swallowing it.
    monkeypatch.setattr(
        "anki_miner.utils.subtitle_encoding._detect_encoding",
        lambda _path: None,
    )
    path = tmp_path / "utf16_no_detector.srt"
    path.write_bytes(_UTF16_DETECTOR_TEXT.encode("utf-16"))

    with pytest.raises(UnicodeDecodeError):
        load_subtitle_events(path)


# ---------------------------------------------------------------------------
# write_condensed_srt / write_condensed_lrc
# ---------------------------------------------------------------------------


def test_write_condensed_srt_roundtrips_via_segments_to_srt(tmp_path):
    events = [(1000, 2000, "Hi"), (2500, 4000, "Bye")]
    path = tmp_path / "out.srt"
    write_condensed_srt(events, path)

    reloaded = pysubs2.load(str(path), format_="srt")
    got = [(e.start, e.end, e.text) for e in reloaded]
    assert got == [(1000, 2000, "Hi"), (2500, 4000, "Bye")]


def test_write_condensed_lrc_golden_string(tmp_path):
    events = [(1000, 2000, "Hello"), (90500, 91000, "World")]
    path = tmp_path / "out.lrc"
    write_condensed_lrc(events, path)

    expected = "[00:01.00]Hello\n[00:02.00]\n[01:30.50]World\n[01:31.00]\n"
    assert path.read_text(encoding="utf-8") == expected


def test_write_condensed_lrc_minutes_exceed_59(tmp_path):
    events = [(3660000, 3661000, "late")]  # 61 minutes
    path = tmp_path / "long.lrc"
    write_condensed_lrc(events, path)
    assert path.read_text(encoding="utf-8") == "[61:00.00]late\n[61:01.00]\n"


# ===========================================================================
# Part 2 — AudioCondenserService (ffmpeg orchestration, faked subprocess)
# ===========================================================================

_POPEN = "anki_miner.services.audio_condenser.subprocess.Popen"
_RESOLVE = "anki_miner.services.audio_condenser.resolve_ffmpeg"


class _FakePopen:
    """Minimal subprocess.Popen stand-in for the streaming runner.

    ``stdout`` is a concrete iterator (assigned once, like the real pipe). The
    cancel test replaces it with a blocking generator.
    """

    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        self.stdout = iter(lines)
        self._final_rc = returncode
        self.returncode: int | None = None
        self.kill_calls = 0

    def wait(self) -> int:
        self.returncode = self._final_rc
        return self._final_rc

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9


def _factory(captured: dict, lines: list[str], returncode: int = 0):
    """Popen side_effect: capture cmd/kwargs/graph, return a fake proc."""

    def _make(cmd: list[str], **kwargs: Any) -> _FakePopen:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        if "-filter_script:a" in cmd:
            graph_path = cmd[cmd.index("-filter_script:a") + 1]
            captured["graph"] = Path(graph_path).read_text(encoding="utf-8")
        fake = _FakePopen(lines, returncode)
        captured["proc"] = fake
        return fake

    return _make


def _progress_block(us: int, *, end: bool = False, include_us: bool = True, include_ms: bool = True) -> list[str]:
    """One ffmpeg ``-progress`` block; ``us`` is the out_time in MICROSECONDS."""
    lines = ["frame=100", "fps=25.0", "bitrate=  96.0kbits/s"]
    if include_us:
        lines.append(f"out_time_us={us}")
    if include_ms:
        lines.append(f"out_time_ms={us}")  # ffmpeg quirk: also microseconds
    lines.append("speed=9.5x")
    lines.append("progress=" + ("end" if end else "continue"))
    return lines


def _make_config(tmp_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.media_temp_folder = tmp_path
    cfg.ffmpeg_location = None
    cfg.ffprobe_location = None
    return cfg


def _make_extractor(global_index: int | None = None, encoder_ok: bool = True) -> MagicMock:
    ext = MagicMock()
    ext._resolve_audio_track_global_index.return_value = global_index
    ext._check_encoder_available.return_value = encoder_ok
    return ext


def _service(tmp_path: Path, *, global_index: int | None = None, encoder_ok: bool = True) -> AudioCondenserService:
    return AudioCondenserService(_make_config(tmp_path), _make_extractor(global_index, encoder_ok))


# --- build_aselect_graph (pure) --------------------------------------------


def test_build_aselect_graph_single_period_float_seconds():
    assert build_aselect_graph([(0, 2000)]) == "aselect='between(t,0.000,2.000)',asetpts=N/SR/TB"


def test_build_aselect_graph_multi_period_ms_to_seconds():
    graph = build_aselect_graph([(500, 1500), (3000, 4200)])
    assert graph == "aselect='between(t,0.500,1.500)+between(t,3.000,4.200)',asetpts=N/SR/TB"


# --- condense: command shape -----------------------------------------------


def test_condense_uses_resolved_ffmpeg_and_progress_header(tmp_path):
    svc = _service(tmp_path, global_index=None)
    captured: dict = {}
    with (
        patch(_RESOLVE, return_value="/bundled/ffmpeg"),
        patch(_POPEN, side_effect=_factory(captured, _progress_block(0, end=True))),
    ):
        ok = svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.mp3")

    assert ok is True
    cmd = captured["cmd"]
    assert cmd[0] == "/bundled/ffmpeg"
    assert cmd[:6] == ["/bundled/ffmpeg", "-y", "-hide_banner", "-nostdin", "-progress", "pipe:1"]
    assert cmd[-1] == str(tmp_path / "out.mp3")
    # stderr must be merged into the read pipe (undrained PIPE deadlocks ffmpeg).
    assert captured["kwargs"]["stderr"] == subprocess.STDOUT
    assert captured["kwargs"]["stdout"] == subprocess.PIPE


def test_condense_map_fallback_when_track_resolution_none(tmp_path):
    svc = _service(tmp_path, global_index=None)
    captured: dict = {}
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory(captured, _progress_block(0, end=True))),
    ):
        svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.mp3")

    cmd = captured["cmd"]
    assert cmd[cmd.index("-map") + 1] == "0:a:0"


def test_condense_map_uses_global_index_when_resolved(tmp_path):
    svc = _service(tmp_path, global_index=3)
    captured: dict = {}
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory(captured, _progress_block(0, end=True))),
    ):
        svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.mp3", audio_track_override=1)

    cmd = captured["cmd"]
    assert cmd[cmd.index("-map") + 1] == "0:3"
    svc.extractor._resolve_audio_track_global_index.assert_called_once_with(Path("/v/in.mkv"), 1)


def test_condense_disables_video_subtitle_data_streams(tmp_path):
    svc = _service(tmp_path, global_index=0)
    captured: dict = {}
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory(captured, _progress_block(0, end=True))),
    ):
        svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.mp3")

    cmd = captured["cmd"]
    for flag in ("-vn", "-sn", "-dn"):
        assert flag in cmd
    assert "-filter_script:a" in cmd


def test_condense_graph_file_content_between_seconds_and_asetpts(tmp_path):
    svc = _service(tmp_path, global_index=0)
    captured: dict = {}
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory(captured, _progress_block(0, end=True))),
    ):
        svc.condense(Path("/v/in.mkv"), [(0, 2000), (3000, 5000)], tmp_path / "out.mp3")

    assert captured["graph"] == ("aselect='between(t,0.000,2.000)+between(t,3.000,5.000)',asetpts=N/SR/TB")


# --- condense: encoder / bitrate / channel mapping -------------------------


def test_condense_mp3_encoder_bitrate_no_downmix(tmp_path):
    svc = _service(tmp_path, global_index=0)
    captured: dict = {}
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory(captured, _progress_block(0, end=True))),
    ):
        svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.mp3", bitrate_kbps=128)

    cmd = captured["cmd"]
    assert cmd[cmd.index("-c:a") + 1] == "libmp3lame"
    assert cmd[cmd.index("-b:a") + 1] == "128k"
    assert "-ac" not in cmd


def test_condense_opus_encoder_bitrate_and_stereo_downmix(tmp_path):
    svc = _service(tmp_path, global_index=0)
    captured: dict = {}
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory(captured, _progress_block(0, end=True))),
    ):
        svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.opus", bitrate_kbps=64)

    cmd = captured["cmd"]
    assert cmd[cmd.index("-c:a") + 1] == "libopus"
    assert cmd[cmd.index("-b:a") + 1] == "64k"
    assert cmd[cmd.index("-ac") + 1] == "2"


def test_condense_flac_no_bitrate_no_downmix_no_probe(tmp_path):
    # encoder_ok=False would raise IF flac were probed — it must not be.
    svc = _service(tmp_path, global_index=0, encoder_ok=False)
    captured: dict = {}
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory(captured, _progress_block(0, end=True))),
    ):
        ok = svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.flac")

    assert ok is True
    cmd = captured["cmd"]
    assert cmd[cmd.index("-c:a") + 1] == "flac"
    assert "-b:a" not in cmd
    assert "-ac" not in cmd
    svc.extractor._check_encoder_available.assert_not_called()


# --- condense: progress parsing (microsecond guard, D7) --------------------


def test_condense_progress_out_time_us_is_microseconds(tmp_path):
    svc = _service(tmp_path, global_index=0)
    pcts: list[int] = []
    # 14675 ms of kept audio; a mid-run out_time of 7_337_506 us == 7337.506 ms
    # => 49% (proves microsecond interpretation; a ms reading would jump to 100).
    lines = _progress_block(0) + _progress_block(7_337_506) + _progress_block(14_675_012, end=True)
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory({}, lines)),
    ):
        ok = svc.condense(Path("/v/in.mkv"), [(0, 14675)], tmp_path / "out.mp3", progress_cb=pcts.append)

    assert ok is True
    assert pcts[0] == 0
    assert 50 in pcts  # ~50%, NOT 100 — microseconds, not milliseconds
    assert pcts[-1] == 100
    assert pcts == sorted(pcts)  # monotonic


def test_condense_progress_out_time_ms_fallback_is_microseconds(tmp_path):
    svc = _service(tmp_path, global_index=0)
    pcts: list[int] = []
    # Only out_time_ms present — still MICROSECONDS (ffmpeg trac #7345).
    lines = _progress_block(7_337_506, include_us=False) + _progress_block(14_675_012, end=True, include_us=False)
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory({}, lines)),
    ):
        svc.condense(Path("/v/in.mkv"), [(0, 14675)], tmp_path / "out.mp3", progress_cb=pcts.append)

    assert 50 in pcts  # ~50%, not the 100 a millisecond misread would clamp to
    assert pcts[-1] == 100


# --- condense: cancellation / errors / guards ------------------------------


def test_condense_cancel_kills_process(tmp_path):
    svc = _service(tmp_path, global_index=0)
    cancel_event = threading.Event()
    killed = threading.Event()

    class _CancelFake:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.kill_calls = 0
            self.stdout = self._gen()

        def _gen(self):
            yield "frame=1"
            cancel_event.set()
            killed.wait(timeout=5.0)  # block "alive" until the watcher kills us
            yield "progress=continue"

        def wait(self) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.kill_calls += 1
            self.returncode = -9
            killed.set()

    fake = _CancelFake()

    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=lambda cmd, **kw: fake),
    ):
        ok = svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.mp3", cancel_event=cancel_event)

    assert ok is False
    assert fake.kill_calls >= 1
    assert killed.is_set()


def test_condense_raises_encoder_unavailable_without_running_ffmpeg(tmp_path):
    svc = _service(tmp_path, global_index=0, encoder_ok=False)
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN) as mock_popen,
        pytest.raises(EncoderUnavailableError),
    ):
        svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.mp3")
    mock_popen.assert_not_called()
    assert list(tmp_path.glob("condense_graph_*.txt")) == []


def test_condense_empty_periods_returns_false_without_ffmpeg(tmp_path):
    svc = _service(tmp_path, global_index=0)
    with patch(_POPEN) as mock_popen:
        ok = svc.condense(Path("/v/in.mkv"), [], tmp_path / "out.mp3")
    assert ok is False
    mock_popen.assert_not_called()


def test_condense_unsupported_suffix_raises_value_error(tmp_path):
    svc = _service(tmp_path, global_index=0)
    with patch(_POPEN) as mock_popen, pytest.raises(ValueError):
        svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.wav")
    mock_popen.assert_not_called()


def test_condense_graph_temp_cleaned_on_success(tmp_path):
    svc = _service(tmp_path, global_index=0)
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory({}, _progress_block(0, end=True), returncode=0)),
    ):
        ok = svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.mp3")
    assert ok is True
    assert list(tmp_path.glob("condense_graph_*.txt")) == []


def test_condense_graph_temp_cleaned_on_failure(tmp_path):
    svc = _service(tmp_path, global_index=0)
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory({}, ["Conversion failed!"], returncode=1)),
    ):
        ok = svc.condense(Path("/v/in.mkv"), [(0, 2000)], tmp_path / "out.mp3")
    assert ok is False
    assert list(tmp_path.glob("condense_graph_*.txt")) == []


def test_condense_removes_partial_output_on_failure(tmp_path):
    """A failed ffmpeg run must not leave a truncated ``<stem>_condensed.mp3``.

    ffmpeg's ``-y`` writes the output non-atomically, so a crash/timeout leaves a
    corrupt partial that the next run's skip gate would treat as complete.
    """
    svc = _service(tmp_path, global_index=0)
    out_audio = tmp_path / "ep01_condensed.mp3"

    def _make(cmd: list[str], **kwargs: Any) -> _FakePopen:
        Path(cmd[-1]).write_text("partial", encoding="utf-8")  # simulate ffmpeg -y partial write
        return _FakePopen(["Conversion failed!"], returncode=1)

    with patch(_RESOLVE, return_value="ffmpeg"), patch(_POPEN, side_effect=_make):
        ok = svc.condense(Path("/v/ep01.mkv"), [(0, 2000)], out_audio)

    assert ok is False
    assert not out_audio.exists()


# --- extract_embedded_subtitle ---------------------------------------------


def _sub_stream(*, sub_index: int, codec: str | None, is_text: bool = True) -> SubtitleStream:
    return SubtitleStream(
        index=sub_index + 1,
        sub_index=sub_index,
        codec_name=codec,
        language_tag="jpn",
        title=None,
        is_text=is_text,
    )


def test_extract_embedded_subtitle_map_and_srt_extension(tmp_path):
    svc = _service(tmp_path)
    stream = _sub_stream(sub_index=1, codec="subrip")
    captured: dict = {}
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory(captured, [], returncode=0)),
    ):
        out = svc.extract_embedded_subtitle(Path("/v/ep01.mkv"), stream, tmp_path)

    cmd = captured["cmd"]
    assert cmd[cmd.index("-map") + 1] == "0:s:1"
    assert "-progress" not in cmd  # extraction runs the runner without progress
    assert out == tmp_path / "ep01.s1.srt"


@pytest.mark.parametrize("codec", ["ass", "ssa"])
def test_extract_embedded_subtitle_ass_extension(tmp_path, codec):
    svc = _service(tmp_path)
    stream = _sub_stream(sub_index=0, codec=codec)
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory({}, [], returncode=0)),
    ):
        out = svc.extract_embedded_subtitle(Path("/v/ep01.mkv"), stream, tmp_path)
    assert out == tmp_path / "ep01.s0.ass"


@pytest.mark.parametrize("codec", ["subrip", "webvtt", "mov_text"])
def test_extract_embedded_subtitle_srt_extension_for_text_codecs(tmp_path, codec):
    svc = _service(tmp_path)
    stream = _sub_stream(sub_index=2, codec=codec)
    with (
        patch(_RESOLVE, return_value="ffmpeg"),
        patch(_POPEN, side_effect=_factory({}, [], returncode=0)),
    ):
        out = svc.extract_embedded_subtitle(Path("/v/ep01.mkv"), stream, tmp_path)
    assert out == tmp_path / "ep01.s2.srt"


def test_extract_embedded_subtitle_refuses_bitmap_without_running_ffmpeg(tmp_path):
    svc = _service(tmp_path)
    stream = _sub_stream(sub_index=0, codec="hdmv_pgs_subtitle", is_text=False)
    with patch(_POPEN) as mock_popen:
        out = svc.extract_embedded_subtitle(Path("/v/ep01.mkv"), stream, tmp_path)
    assert out is None
    mock_popen.assert_not_called()


def test_extract_embedded_subtitle_cleans_partial_on_failure(tmp_path):
    svc = _service(tmp_path)
    stream = _sub_stream(sub_index=0, codec="subrip")

    def _make(cmd: list[str], **kwargs: Any) -> _FakePopen:
        Path(cmd[-1]).write_text("partial", encoding="utf-8")  # simulate a partial write
        return _FakePopen([], returncode=1)

    with patch(_RESOLVE, return_value="ffmpeg"), patch(_POPEN, side_effect=_make):
        out = svc.extract_embedded_subtitle(Path("/v/ep01.mkv"), stream, tmp_path)

    assert out is None
    assert not (tmp_path / "ep01.s0.srt").exists()

"""Utilities → Tracks service: codec table, naming, planning, and the ffmpeg call (faked).

The real-ffmpeg round trip of every codec row is test_track_extractor_real_ffmpeg.py.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import FfmpegNotFoundError
from anki_miner.services import track_extractor as te
from anki_miner.services.track_extractor import (
    AUDIO_FORMATS,
    SUBTITLE_FORMATS,
    TRACKS_VIDEO_EXTENSIONS,
    ExtractStatus,
    InputProbe,
    MediaTracks,
    PlannedTrack,
    TrackExtractorService,
    TrackRef,
    build_command,
    list_videos,
    output_name,
    plan_outputs,
    preferred_refs,
    probe_input,
    track_format,
)
from anki_miner.utils import file_utils
from anki_miner.utils.audio_track_detector import BITMAP_SUBTITLE_CODECS, AudioStream, SubtitleStream
from anki_miner.utils.process_supervisor import SupervisedResult, SupervisedState

JA = frozenset({"ja", "jpn", "japanese", "jp"})
SUB = "subtitle"
AUD = "audio"


def _sub(pos: int, codec: str | None = "ass", lang: str | None = "jpn", *, forced: bool = False) -> SubtitleStream:
    return SubtitleStream(
        index=pos + 2,
        sub_index=pos,
        codec_name=codec,
        language_tag=lang,
        title=None,
        is_text=codec not in BITMAP_SUBTITLE_CODECS,
        is_forced=forced,
        is_default=pos == 0,
    )


def _aud(pos: int, codec: str | None = "aac", lang: str | None = "jpn") -> AudioStream:
    return AudioStream(
        global_index=pos + 1,
        audio_index=pos,
        language_tag=lang,
        title_tag=None,
        codec=codec,
        channels=2,
        is_default=pos == 0,
    )


# --- codec table -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "codec", "suffix", "muxer", "codec_arg"),
    [
        (SUB, "ass", ".ass", "ass", "copy"),
        (SUB, "ssa", ".ass", "ass", "copy"),
        (SUB, "subrip", ".srt", "srt", "copy"),
        (SUB, "webvtt", ".vtt", "webvtt", "copy"),
        (SUB, "mov_text", ".srt", "srt", "subrip"),
        (SUB, "hdmv_pgs_subtitle", ".sup", "sup", "copy"),
        (SUB, "dvd_subtitle", ".mks", "matroska", "copy"),
        (SUB, "dvb_subtitle", ".mks", "matroska", "copy"),
        (SUB, None, ".mks", "matroska", "copy"),
        (AUD, "aac", ".m4a", "ipod", "copy"),
        (AUD, "alac", ".m4a", "ipod", "copy"),
        (AUD, "opus", ".opus", "opus", "copy"),
        (AUD, "vorbis", ".ogg", "ogg", "copy"),
        (AUD, "flac", ".flac", "flac", "copy"),
        (AUD, "mp3", ".mp3", "mp3", "copy"),
        (AUD, "ac3", ".mka", "matroska", "copy"),
        (AUD, "truehd", ".mka", "matroska", "copy"),
        (AUD, None, ".mka", "matroska", "copy"),
    ],
)
def test_track_format_rows(kind, codec, suffix, muxer, codec_arg):
    fmt = track_format(kind, codec)
    assert (fmt.suffix, fmt.muxer, fmt.codec) == (suffix, muxer, codec_arg)


def test_suffix_sets_are_disjoint():
    """An audio output can never be taken for a subtitle, and neither for a video input."""
    subs = {f.suffix for f in SUBTITLE_FORMATS.values()} | {track_format(SUB, None).suffix}
    audio = {f.suffix for f in AUDIO_FORMATS.values()} | {track_format(AUD, None).suffix}
    assert not subs & audio
    assert not (subs | audio) & TRACKS_VIDEO_EXTENSIONS


# --- naming ------------------------------------------------------------------


def test_output_name_sole_track_is_the_stem(tmp_path):
    fmt = track_format(SUB, "ass")
    assert output_name("EP01", TrackRef(SUB, 1), "jpn", fmt, sole_of_kind=True, directory=tmp_path) == "EP01.ass"


def test_output_name_several_carry_number_and_language(tmp_path):
    assert (
        output_name("EP01", TrackRef(SUB, 1), "jpn", track_format(SUB, "ass"), sole_of_kind=False, directory=tmp_path)
        == "EP01.s2.jpn.ass"
    )
    assert (
        output_name("EP01", TrackRef(AUD, 0), None, track_format(AUD, "aac"), sole_of_kind=False, directory=tmp_path)
        == "EP01.a1.m4a"
    )


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("../x", "EP01.s1.ass"), ("und", "EP01.s1.ass"), ("pt-br", "EP01.s1.pt-br.ass"), ("JPN", "EP01.s1.jpn.ass")],
)
def test_language_tag_is_sanitised(tmp_path, tag, expected):
    fmt = track_format(SUB, "ass")
    assert output_name("EP01", TrackRef(SUB, 0), tag, fmt, sole_of_kind=False, directory=tmp_path) == expected


def test_long_stem_is_bounded_and_keeps_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(file_utils, "component_byte_limit", lambda _d: 40)
    name = output_name(
        "猫" * 60, TrackRef(SUB, 0), "jpn", track_format(SUB, "ass"), sole_of_kind=True, directory=tmp_path
    )
    assert name.endswith(".ass")
    assert len(name.encode("utf-8")) <= 40


# --- planning ----------------------------------------------------------------


def test_plan_matches_by_position_and_reports_missing(tmp_path):
    video = tmp_path / "EP01.mkv"
    tracks = MediaTracks(subtitles=(_sub(0), _sub(1, "subrip", "eng")), audio=(_aud(0),))
    ticked = [TrackRef(SUB, 1), TrackRef(AUD, 0), TrackRef(SUB, 5)]
    planned, missing = plan_outputs(video, tracks, ticked, tmp_path)
    assert [p.dest.name for p in planned] == ["EP01.s2.eng.srt", "EP01.m4a"]
    assert [p.stream for p in planned] == [tracks.subtitles[1], tracks.audio[0]]
    assert missing == [TrackRef(SUB, 5)]


def test_sole_of_kind_follows_the_ticked_set_not_the_file(tmp_path):
    """Two subtitles ticked, the video has one: names must match the other episodes'."""
    tracks = MediaTracks(subtitles=(_sub(0),))
    planned, missing = plan_outputs(tmp_path / "EP07.mkv", tracks, [TrackRef(SUB, 0), TrackRef(SUB, 1)], tmp_path)
    assert [p.dest.name for p in planned] == ["EP07.s1.jpn.ass"]
    assert missing == [TrackRef(SUB, 1)]


def test_plan_writes_into_the_chosen_folder(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    planned, _ = plan_outputs(tmp_path / "EP01.mkv", MediaTracks(subtitles=(_sub(0),)), [TrackRef(SUB, 0)], out)
    assert planned[0].dest == out / "EP01.ass"


# --- pre-tick ----------------------------------------------------------------


def test_preferred_refs_ticks_the_mining_language_text_track():
    tracks = MediaTracks(subtitles=(_sub(0, "subrip", "eng"), _sub(1, "ass", "jpn")), audio=(_aud(0),))
    assert preferred_refs(tracks, JA) == (TrackRef(SUB, 1),)


def test_preferred_refs_prefers_full_over_forced():
    tracks = MediaTracks(subtitles=(_sub(0, "ass", "jpn", forced=True), _sub(1, "ass", "jpn")))
    assert preferred_refs(tracks, JA) == (TrackRef(SUB, 1),)


def test_preferred_refs_never_picks_a_bitmap_track():
    assert preferred_refs(MediaTracks(subtitles=(_sub(0, "hdmv_pgs_subtitle", "jpn"),)), JA) == ()


@pytest.mark.parametrize("tag", [None, "und"])
def test_preferred_refs_ticks_a_lone_untagged_track(tag):
    assert preferred_refs(MediaTracks(subtitles=(_sub(0, "ass", tag),)), JA) == (TrackRef(SUB, 0),)


def test_preferred_refs_leaves_a_lone_other_language_track_unticked():
    """A lone English .srt named EP01.srt would pair into Japanese mining."""
    assert preferred_refs(MediaTracks(subtitles=(_sub(0, "subrip", "eng"),)), JA) == ()


def test_preferred_refs_never_ticks_audio():
    assert preferred_refs(MediaTracks(audio=(_aud(0),)), JA) == ()


# --- listing and probing -----------------------------------------------------


def test_list_videos_filters_and_sorts_naturally(tmp_path):
    for name in ("EP10.mkv", "EP2.mkv", "EP1.webm", "EP1.srt", "._EP3.mkv"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "sub.mkv").mkdir()
    assert [p.name for p in list_videos(tmp_path)] == ["EP1.webm", "EP2.mkv", "EP10.mkv"]


@pytest.fixture
def fake_probe(monkeypatch):
    calls: list[tuple[Path, str]] = []

    def subs(video: Path, ffprobe_cmd: str = "ffprobe"):
        calls.append((video, ffprobe_cmd))
        return [_sub(0)]

    monkeypatch.setattr(te, "list_subtitle_streams", subs)
    monkeypatch.setattr(te, "list_audio_streams", lambda video, ffprobe_cmd="ffprobe": [_aud(0)])
    return calls


def test_probe_input_file(tmp_path, fake_probe):
    video = tmp_path / "EP01.mkv"
    video.write_bytes(b"x")
    probe = probe_input(video, "/opt/ffprobe", JA)
    assert probe == InputProbe(video, (video,), MediaTracks((_sub(0),), (_aud(0),)), (TrackRef(SUB, 0),))
    assert fake_probe == [(video, "/opt/ffprobe")]


def test_probe_input_folder_reads_the_first_video(tmp_path, fake_probe):
    for name in ("EP2.mkv", "EP10.mkv"):
        (tmp_path / name).write_bytes(b"x")
    probe = probe_input(tmp_path, "ffprobe", JA)
    assert [v.name for v in probe.videos] == ["EP2.mkv", "EP10.mkv"]
    assert fake_probe == [(tmp_path / "EP2.mkv", "ffprobe")]


def test_probe_input_empty_folder(tmp_path, fake_probe):
    probe = probe_input(tmp_path, "ffprobe", JA)
    assert probe.videos == () and probe.tracks.is_empty and probe.preselected == ()
    assert fake_probe == []


# --- command -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "stream", "flag", "codec_arg", "muxer", "mapped"),
    [
        (TrackRef(SUB, 0), _sub(0, "ass"), "-c:s", "copy", "ass", "0:2"),
        (TrackRef(SUB, 0), _sub(0, "mov_text"), "-c:s", "subrip", "srt", "0:2"),
        (TrackRef(SUB, 0), _sub(0, "hdmv_pgs_subtitle"), "-c:s", "copy", "sup", "0:2"),
        (TrackRef(AUD, 1), _aud(1, "aac"), "-c:a", "copy", "ipod", "0:2"),
        (TrackRef(AUD, 0), _aud(0, "ac3"), "-c:a", "copy", "matroska", "0:1"),
    ],
)
def test_build_command_copies_one_stream_into_the_tables_muxer(tmp_path, ref, stream, flag, codec_arg, muxer, mapped):
    video = tmp_path / "Re:Zero 01.mkv"
    fmt = track_format(ref.kind, stream.codec_name if isinstance(stream, SubtitleStream) else stream.codec)
    plan = PlannedTrack(ref, stream, fmt, tmp_path / f"out{fmt.suffix}")
    staged = tmp_path / f".anki-miner-x{fmt.suffix}"
    cmd = build_command("ffmpeg", video, plan, staged)
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-i") + 1] == str(video.absolute())
    assert cmd[cmd.index("-map") + 1] == mapped
    assert cmd[cmd.index(flag) + 1] == codec_arg
    assert cmd[cmd.index("-f") + 1] == muxer
    assert "-y" in cmd and "-nostdin" in cmd
    assert cmd[-1] == str(staged.absolute())


# --- extract -----------------------------------------------------------------


def _fake_run(
    monkeypatch,
    *,
    payload: bytes | None = b"data",
    state: SupervisedState = SupervisedState.COMPLETED,
    returncode: int | None = 0,
    stderr: str = "",
    error: BaseException | None = None,
) -> list[tuple[list[str], dict[str, Any]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        if payload is not None:
            Path(cmd[-1]).write_bytes(payload)
        return SupervisedResult(state, returncode, "", stderr, error)

    monkeypatch.setattr(te, "run_supervised", fake)
    monkeypatch.setattr(te, "resolve_ffmpeg", lambda _config: "ffmpeg")
    return calls


def _plan(tmp_path: Path) -> tuple[Path, PlannedTrack]:
    video = tmp_path / "EP01.mkv"
    video.write_bytes(b"video")
    planned, _ = plan_outputs(video, MediaTracks(subtitles=(_sub(0),)), [TrackRef(SUB, 0)], tmp_path)
    return video, planned[0]


def _leftovers(folder: Path) -> list[str]:
    return [p.name for p in folder.iterdir() if p.name.startswith(".anki-miner-")]


def test_extract_publishes_atomically(tmp_path, monkeypatch):
    calls = _fake_run(monkeypatch)
    video, plan = _plan(tmp_path)
    result = TrackExtractorService(AnkiMinerConfig()).extract(video, plan)
    assert result.status is ExtractStatus.SAVED
    assert plan.dest.read_bytes() == b"data"
    staged = Path(calls[0][0][-1])
    assert staged.suffix == plan.dest.suffix and staged.parent == plan.dest.parent
    assert _leftovers(tmp_path) == []
    assert video.read_bytes() == b"video"


def test_extract_hands_cancel_timeout_and_op_to_the_supervisor(tmp_path, monkeypatch):
    calls = _fake_run(monkeypatch)
    video, plan = _plan(tmp_path)
    cancel = threading.Event()
    TrackExtractorService(AnkiMinerConfig()).extract(video, plan, cancel_event=cancel)
    kwargs = calls[0][1]
    assert kwargs["cancel"] is cancel
    assert kwargs["timeout_s"] == te._EXTRACT_TIMEOUT_S
    assert kwargs["op"] == "ffmpeg track-extract"


def test_extract_failure_reports_ffmpegs_last_line_and_leaves_nothing(tmp_path, monkeypatch):
    _fake_run(monkeypatch, state=SupervisedState.FAILED, returncode=1, stderr="noise\nInvalid data found\n")
    video, plan = _plan(tmp_path)
    result = TrackExtractorService(AnkiMinerConfig()).extract(video, plan)
    assert result.status is ExtractStatus.FAILED and result.reason == "Invalid data found"
    assert not plan.dest.exists() and _leftovers(tmp_path) == []


def test_extract_timeout_fails(tmp_path, monkeypatch):
    _fake_run(monkeypatch, state=SupervisedState.TIMED_OUT, returncode=None)
    video, plan = _plan(tmp_path)
    result = TrackExtractorService(AnkiMinerConfig()).extract(video, plan)
    assert result.status is ExtractStatus.FAILED and "timed out" in result.reason


def test_extract_empty_output_fails(tmp_path, monkeypatch):
    _fake_run(monkeypatch, payload=b"")
    video, plan = _plan(tmp_path)
    result = TrackExtractorService(AnkiMinerConfig()).extract(video, plan)
    assert result.status is ExtractStatus.FAILED and not plan.dest.exists()


def test_extract_cancel_returns_cancelled_and_leaves_nothing(tmp_path, monkeypatch):
    _fake_run(monkeypatch, state=SupervisedState.CANCELLED, returncode=None)
    video, plan = _plan(tmp_path)
    result = TrackExtractorService(AnkiMinerConfig()).extract(video, plan)
    assert result.status is ExtractStatus.CANCELLED
    assert not plan.dest.exists() and _leftovers(tmp_path) == []


def test_existing_dest_survives_a_failed_extract(tmp_path, monkeypatch):
    _fake_run(monkeypatch, state=SupervisedState.FAILED, returncode=1)
    video, plan = _plan(tmp_path)
    plan.dest.write_bytes(b"mine")
    TrackExtractorService(AnkiMinerConfig()).extract(video, plan)
    assert plan.dest.read_bytes() == b"mine"


def test_extract_replaces_an_existing_dest_on_success(tmp_path, monkeypatch):
    _fake_run(monkeypatch, payload=b"new")
    video, plan = _plan(tmp_path)
    plan.dest.write_bytes(b"old")
    assert TrackExtractorService(AnkiMinerConfig()).extract(video, plan).status is ExtractStatus.SAVED
    assert plan.dest.read_bytes() == b"new"


def test_spawn_failure_raises_ffmpeg_not_found(tmp_path, monkeypatch):
    _fake_run(
        monkeypatch, payload=None, state=SupervisedState.FAILED, returncode=None, error=FileNotFoundError("ffmpeg")
    )
    video, plan = _plan(tmp_path)
    with pytest.raises(FfmpegNotFoundError):
        TrackExtractorService(AnkiMinerConfig()).extract(video, plan)
    assert _leftovers(tmp_path) == []


def test_unwritable_destination_fails_the_track(tmp_path, monkeypatch):
    _fake_run(monkeypatch)
    video, plan = _plan(tmp_path)
    gone = PlannedTrack(plan.ref, plan.stream, plan.fmt, tmp_path / "missing-dir" / "EP01.ass")
    result = TrackExtractorService(AnkiMinerConfig()).extract(video, gone)
    assert result.status is ExtractStatus.FAILED and result.reason


def test_batch_pairs_each_episode_with_its_own_multi_tick_subtitle(tmp_path):
    """Two ticked subtitles per episode must not move one episode's file onto another (Batch mining)."""
    from anki_miner.utils.file_pairing import FilePairMatcher

    tracks = MediaTracks(subtitles=(_sub(0, "ass", "jpn"), _sub(1, "subrip", "eng")))
    videos = [tmp_path / f"Show - {n:02d}.mkv" for n in range(1, 5)]
    for video in videos:
        video.write_bytes(b"video")
        planned, _ = plan_outputs(video, tracks, [TrackRef(SUB, 0), TrackRef(SUB, 1)], tmp_path)
        for plan in planned:
            plan.dest.write_text("x", encoding="utf-8")

    pairs = FilePairMatcher.find_pairs_by_episode_number(tmp_path, tmp_path)

    assert [(p.video.name, p.subtitle.name) for p in pairs] == [
        (f"Show - {n:02d}.mkv", f"Show - {n:02d}.s1.jpn.ass") for n in range(1, 5)
    ]


def test_a_save_that_ffmpeg_complained_about_carries_its_warning(tmp_path, monkeypatch):
    """A half-downloaded .mkv exits 0 with 'File ended prematurely': saved, but not cleanly."""
    _fake_run(monkeypatch, stderr="[matroska,webm @ 0x1] File ended prematurely\n")
    video, plan = _plan(tmp_path)
    result = TrackExtractorService(AnkiMinerConfig()).extract(video, plan)
    assert result.status is ExtractStatus.SAVED
    assert "File ended prematurely" in result.reason


def test_a_clean_save_has_no_reason(tmp_path, monkeypatch):
    _fake_run(monkeypatch)
    video, plan = _plan(tmp_path)
    assert TrackExtractorService(AnkiMinerConfig()).extract(video, plan).reason == ""


def test_ffmpegs_opus_parser_noise_is_not_a_warning(tmp_path, monkeypatch):
    """ffmpeg 8 prints this on every clean copy from a source with Opus audio (real-ffmpeg test pins it)."""
    _fake_run(monkeypatch, stderr="[opus @ 0x5fa3e1815580] Error parsing Opus packet header.\n")
    video, plan = _plan(tmp_path)
    assert TrackExtractorService(AnkiMinerConfig()).extract(video, plan).reason == ""


def test_real_damage_still_shows_past_opus_noise(tmp_path, monkeypatch):
    _fake_run(
        monkeypatch,
        stderr="[opus @ 0x1] Error parsing Opus packet header.\n[matroska,webm @ 0x2] File ended prematurely\n"
        "[opus @ 0x1] Error parsing Opus packet header.\n",
    )
    video, plan = _plan(tmp_path)
    assert "File ended prematurely" in TrackExtractorService(AnkiMinerConfig()).extract(video, plan).reason

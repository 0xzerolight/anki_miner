"""Tests for CondenseWorker — signal sequence, skip/overwrite, per-file error
isolation, cancel, subtitle-source priority, zero-period paths, encoder-missing
queue abort, embedded-sub temp cleanup, and condensed-sidecar writing.

The pure interval-math + subtitle-I/O pipeline (load/shift/filter/build/map) is
exercised for real through tiny on-disk ``.srt``/``.ass`` fixtures; only
``AudioCondenserService`` and the ffprobe/discovery helpers
(``list_subtitle_streams`` / ``find_sibling_subtitle`` / ``resolve_ffprobe``) are
faked. No real ffmpeg or ffprobe is invoked.
"""

from __future__ import annotations

from pathlib import Path

import pysubs2
import pytest

pytest.importorskip("PyQt6.QtCore")

import anki_miner.gui.workers.condense_worker as cw
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers.condense_worker import CondenseItem, CondenseWorker
from anki_miner.services.audio_condenser import EncoderUnavailableError
from anki_miner.utils.audio_track_detector import SubtitleStream

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(media_temp_folder=tmp_path / "temp")


def _write_srt(path: Path, cues: list[tuple[int, int, str]]) -> Path:
    """Write a real .srt file with the given (start_ms, end_ms, text) cues."""
    subs = pysubs2.SSAFile()
    for start, end, text in cues:
        subs.append(pysubs2.SSAEvent(start=start, end=end, text=text))
    subs.save(str(path), format_="srt")
    return path


def _write_all_comment_ass(path: Path) -> Path:
    """Write an .ass file whose every event is a Comment (no dialogue)."""
    subs = pysubs2.SSAFile()
    for i in range(3):
        ev = pysubs2.SSAEvent(start=i * 1000, end=i * 1000 + 500, text=f"c{i}")
        ev.type = "Comment"
        subs.append(ev)
    subs.save(str(path), format_="ass")
    return path


def _sub_stream(*, sub_index: int, codec: str, lang: str | None = "jpn", is_text: bool = True) -> SubtitleStream:
    return SubtitleStream(
        index=sub_index + 1,
        sub_index=sub_index,
        codec_name=codec,
        language_tag=lang,
        title=None,
        is_text=is_text,
    )


class _FakeService:
    """Injected AudioCondenserService stand-in — records calls, writes real files."""

    def __init__(
        self,
        *,
        condense_result: bool = True,
        encoder_error: bool = False,
        cancel_on_condense: bool = False,
        extract_returns: bool = True,
    ) -> None:
        self._condense_result = condense_result
        self._encoder_error = encoder_error
        self._cancel_on_condense = cancel_on_condense
        self._extract_returns = extract_returns
        self.condense_calls: list[dict] = []
        self.extract_calls: list[dict] = []

    def extract_embedded_subtitle(self, video, stream, out_dir, cancel_event=None):
        self.extract_calls.append({"video": video, "stream": stream, "out_dir": out_dir})
        if not self._extract_returns:
            return None
        out = Path(out_dir) / f"{video.stem}.s{stream.sub_index}.srt"
        _write_srt(out, [(1000, 2000, "embedded line")])
        return out

    def condense(
        self,
        media,
        periods,
        out_audio,
        *,
        audio_track_override=None,
        bitrate_kbps=96,
        progress_cb=None,
        cancel_event=None,
    ):
        self.condense_calls.append(
            {
                "media": media,
                "periods": list(periods),
                "out_audio": out_audio,
                "audio_track_override": audio_track_override,
                "bitrate_kbps": bitrate_kbps,
            }
        )
        if self._encoder_error:
            raise EncoderUnavailableError("ffmpeg encoder 'libmp3lame' is unavailable")
        if self._cancel_on_condense and cancel_event is not None:
            cancel_event.set()
            return False
        if progress_cb is not None:
            progress_cb(50)
        if self._condense_result:
            Path(out_audio).write_bytes(b"AUDIO")
        return self._condense_result


def _make_worker(items, config, *, service=None, **kwargs) -> CondenseWorker:
    return CondenseWorker(config, items, service=service, **kwargs)


def _capture(worker: CondenseWorker) -> dict:
    cap: dict = {"started": [], "progress": [], "finished": [], "skipped": [], "queue_finished": []}
    worker.file_started.connect(lambda idx: cap["started"].append(idx))
    worker.file_progress.connect(lambda idx, pct, msg: cap["progress"].append((idx, pct, msg)))
    worker.file_finished.connect(lambda idx, out, err: cap["finished"].append((idx, out, err)))
    worker.file_skipped.connect(lambda idx, out: cap["skipped"].append((idx, out)))
    worker.queue_finished.connect(lambda: cap["queue_finished"].append(True))
    return cap


def _no_streams(monkeypatch) -> None:
    """Stub embedded-stream discovery so no ffprobe runs and no streams exist."""
    monkeypatch.setattr(cw, "resolve_ffprobe", lambda config: "ffprobe")
    monkeypatch.setattr(cw, "list_subtitle_streams", lambda media, ffprobe: [])


# ---------------------------------------------------------------------------
# Happy path: external sub, success signal sequence
# ---------------------------------------------------------------------------


def test_happy_path_external_sub_signal_sequence(qapp, tmp_path):
    """External sub → started, progress, finished(out_audio, None), queue_finished once."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01_dialogue.srt", [(1000, 2000, "hello"), (5000, 6000, "world")])

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, sub)], config, service=service)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert cap["started"] == [0]
    assert cap["queue_finished"] == [True]
    idx, out, err = cap["finished"][0]
    assert idx == 0
    assert out == tmp_path / "ep01_condensed.mp3"
    assert err is None
    # Final progress is 100 "Done".
    file_progress = [p for p in cap["progress"] if p[0] == 0]
    assert file_progress[-1][1] == 100
    # Service was called with the built periods (padded 500 default).
    assert len(service.condense_calls) == 1
    assert service.condense_calls[0]["periods"] == [(500, 2500), (4500, 6500)]
    assert service.condense_calls[0]["out_audio"] == tmp_path / "ep01_condensed.mp3"


def test_output_format_and_bitrate_and_track_forwarded(qapp, tmp_path):
    """output_format shapes the filename; bitrate + audio_track_override reach condense."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "hi")])

    service = _FakeService()
    worker = _make_worker(
        [CondenseItem(media, sub)],
        config,
        service=service,
        output_format="opus",
        bitrate_kbps=128,
        audio_track_override=2,
    )
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert cap["finished"][0][1] == tmp_path / "ep01_condensed.opus"
    call = service.condense_calls[0]
    assert call["bitrate_kbps"] == 128
    assert call["audio_track_override"] == 2


def test_output_dir_used_and_created(qapp, tmp_path):
    """output_dir set: audio written there; the directory is created."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "hi")])
    out_dir = tmp_path / "out" / "nested"

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, sub)], config, service=service, output_dir=out_dir)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert out_dir.exists()
    assert cap["finished"][0][1] == out_dir / "ep01_condensed.mp3"


# ---------------------------------------------------------------------------
# Skip-if-exists vs overwrite
# ---------------------------------------------------------------------------


def test_skip_if_exists_no_overwrite(qapp, tmp_path):
    """Existing condensed audio → file_skipped, no file_finished, service untouched."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "hi")])
    existing = tmp_path / "ep01_condensed.mp3"
    existing.write_bytes(b"OLD")

    service = _FakeService()
    worker = _make_worker([CondenseItem(media)], config, service=service, overwrite=False)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert cap["skipped"] == [(0, existing)]
    assert cap["finished"] == []
    assert cap["queue_finished"] == [True]
    assert service.condense_calls == []
    assert any("Skipped" in p[2] for p in cap["progress"] if p[0] == 0 and p[1] == 100)


def test_overwrite_condenses_existing(qapp, tmp_path):
    """overwrite=True → condense runs even when the output already exists."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "hi")])
    (tmp_path / "ep01_condensed.mp3").write_bytes(b"OLD")

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, sub)], config, service=service, overwrite=True)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert len(service.condense_calls) == 1
    assert cap["finished"][0][2] is None


# ---------------------------------------------------------------------------
# Per-file error isolation
# ---------------------------------------------------------------------------


def test_per_file_error_isolation(qapp, tmp_path):
    """File 1 raises inside condense → error forwarded; file 2 still succeeds."""
    config = _make_config(tmp_path)
    m1 = tmp_path / "ep01.mkv"
    m2 = tmp_path / "ep02.mkv"
    m1.write_bytes(b"")
    m2.write_bytes(b"")
    s1 = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "a")])
    s2 = _write_srt(tmp_path / "ep02.srt", [(1000, 2000, "b")])

    calls = {"n": 0}

    class _SelectiveService(_FakeService):
        def condense(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("condense boom")
            return super().condense(*args, **kwargs)

    service = _SelectiveService()
    worker = _make_worker([CondenseItem(m1, s1), CondenseItem(m2, s2)], config, service=service)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert cap["started"] == [0, 1]
    finished = {item[0]: item for item in cap["finished"]}
    assert finished[0][1] is None and "condense boom" in finished[0][2]
    assert finished[1][1] is not None and finished[1][2] is None
    assert cap["queue_finished"] == [True]


def test_condense_false_reports_failure(qapp, tmp_path):
    """condense returns False (not cancelled) → file_finished(None, error)."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "a")])

    service = _FakeService(condense_result=False)
    worker = _make_worker([CondenseItem(media, sub)], config, service=service)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    idx, out, err = cap["finished"][0]
    assert out is None
    assert err is not None and media.name in err
    assert cap["queue_finished"] == [True]


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_between_files_still_emits_queue_finished(qapp, tmp_path):
    """Cancel landing during file 0's condense → file 1 skipped, queue_finished fires."""
    config = _make_config(tmp_path)
    m1 = tmp_path / "ep01.mkv"
    m2 = tmp_path / "ep02.mkv"
    m1.write_bytes(b"")
    m2.write_bytes(b"")
    s1 = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "a")])
    s2 = _write_srt(tmp_path / "ep02.srt", [(1000, 2000, "b")])

    service = _FakeService(cancel_on_condense=True)
    worker = _make_worker([CondenseItem(m1, s1), CondenseItem(m2, s2)], config, service=service)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert 0 in cap["started"]
    assert 1 not in cap["started"]
    finished = {item[0]: item for item in cap["finished"]}
    assert finished[0][1] is None
    assert finished[0][2] == "Cancelled"
    assert cap["queue_finished"] == [True]
    assert worker.is_cancelled is True


def test_cancel_before_run_processes_nothing(qapp, tmp_path):
    """cancel() before run() → no file processed, queue_finished still fires."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "a")])

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, sub)], config, service=service)
    cap = _capture(worker)
    worker.cancel()
    worker.run()
    worker.wait(2000)

    assert cap["started"] == []
    assert service.condense_calls == []
    assert cap["queue_finished"] == [True]


# ---------------------------------------------------------------------------
# Subtitle source resolution: no source, bitmap-only, priority order
# ---------------------------------------------------------------------------


def test_no_source_continues_queue(qapp, tmp_path, monkeypatch):
    """A file with no usable subtitle source → finished(None, msg); queue continues."""
    config = _make_config(tmp_path)
    m1 = tmp_path / "ep01.mkv"
    m2 = tmp_path / "ep02.mkv"
    m1.write_bytes(b"")
    m2.write_bytes(b"")
    s2 = _write_srt(tmp_path / "ep02.srt", [(1000, 2000, "b")])
    _no_streams(monkeypatch)  # ep01 has no sibling and no embedded streams

    service = _FakeService()
    worker = _make_worker([CondenseItem(m1), CondenseItem(m2, s2)], config, service=service)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    finished = {item[0]: item for item in cap["finished"]}
    assert finished[0][1] is None and finished[0][2] is not None  # no-source reason
    assert finished[1][1] is not None and finished[1][2] is None  # ep02 succeeded
    assert cap["queue_finished"] == [True]


def test_bitmap_only_embedded_reports_clear_message(qapp, tmp_path, monkeypatch):
    """Only image-based embedded streams → clear message, service never runs."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    monkeypatch.setattr(cw, "resolve_ffprobe", lambda config: "ffprobe")
    monkeypatch.setattr(
        cw,
        "list_subtitle_streams",
        lambda m, ffprobe: [_sub_stream(sub_index=0, codec="hdmv_pgs_subtitle", is_text=False)],
    )

    service = _FakeService()
    worker = _make_worker([CondenseItem(media)], config, service=service)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    idx, out, err = cap["finished"][0]
    assert out is None
    assert "image-based" in err.lower()
    assert "hdmv_pgs_subtitle" in err
    assert service.condense_calls == []
    assert service.extract_calls == []


def test_external_beats_sibling_beats_embedded(qapp, tmp_path, monkeypatch):
    """Priority: explicit external_sub wins over an on-disk sibling and embedded."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    # Sibling on disk (would be picked if no explicit sub).
    _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "sibling")])
    external = _write_srt(tmp_path / "explicit.srt", [(9000, 10000, "explicit")])

    captured: list[Path] = []
    monkeypatch.setattr(cw, "find_sibling_subtitle", lambda *a, **k: captured.append("sibling") or None)

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, external)], config, service=service)
    worker.run()
    worker.wait(2000)

    # find_sibling_subtitle must never be consulted when an explicit sub is given.
    assert captured == []
    # Periods came from the EXPLICIT sub (9000-10000 padded → 8500-10500).
    assert service.condense_calls[0]["periods"] == [(8500, 10500)]


def test_sibling_beats_embedded(qapp, tmp_path, monkeypatch):
    """No explicit sub → sibling is used and embedded discovery is not consulted."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "sibling")])

    list_calls: list = []
    monkeypatch.setattr(cw, "resolve_ffprobe", lambda config: "ffprobe")
    monkeypatch.setattr(cw, "list_subtitle_streams", lambda m, ffprobe: list_calls.append(1) or [])

    service = _FakeService()
    worker = _make_worker([CondenseItem(media)], config, service=service)
    worker.run()
    worker.wait(2000)

    assert list_calls == []  # embedded probing skipped when a sibling exists
    assert service.condense_calls[0]["periods"] == [(500, 2500)]


def test_embedded_used_when_no_external_or_sibling(qapp, tmp_path, monkeypatch):
    """No explicit/sibling sub → embedded text track is extracted and condensed."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    monkeypatch.setattr(cw, "find_sibling_subtitle", lambda *a, **k: None)
    monkeypatch.setattr(cw, "resolve_ffprobe", lambda config: "ffprobe")
    monkeypatch.setattr(cw, "list_subtitle_streams", lambda m, ffprobe: [_sub_stream(sub_index=0, codec="subrip")])

    service = _FakeService()
    worker = _make_worker([CondenseItem(media)], config, service=service)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert len(service.extract_calls) == 1
    assert cap["finished"][0][2] is None  # success via embedded sub
    # Embedded fake wrote a 1000-2000 cue → periods 500-2500.
    assert service.condense_calls[0]["periods"] == [(500, 2500)]


# ---------------------------------------------------------------------------
# Zero-period paths: condense NOT called
# ---------------------------------------------------------------------------


def test_empty_sub_file_zero_periods_no_condense(qapp, tmp_path):
    """All-comment sub → zero periods → finished(None, msg), condense NOT called."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_all_comment_ass(tmp_path / "ep01.ass")

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, sub)], config, service=service)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    idx, out, err = cap["finished"][0]
    assert out is None
    assert err is not None and "dialogue" in err.lower()
    assert service.condense_calls == []


def test_all_lines_filtered_zero_periods_no_condense(qapp, tmp_path):
    """Every line is SFX/music (filtered_chars empties them) → zero periods, no condense."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "♪♪"), (3000, 4000, "♫")])

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, sub)], config, service=service, filtered_chars="♪♫♬")
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    idx, out, err = cap["finished"][0]
    assert out is None
    assert err is not None and "dialogue" in err.lower()
    assert service.condense_calls == []


# ---------------------------------------------------------------------------
# EncoderUnavailableError → stop the whole queue
# ---------------------------------------------------------------------------


def test_encoder_unavailable_stops_queue(qapp, tmp_path):
    """File 0 hits a missing encoder → file 1 not processed, queue_finished fires."""
    config = _make_config(tmp_path)
    m1 = tmp_path / "ep01.mkv"
    m2 = tmp_path / "ep02.mkv"
    m1.write_bytes(b"")
    m2.write_bytes(b"")
    s1 = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "a")])
    s2 = _write_srt(tmp_path / "ep02.srt", [(1000, 2000, "b")])

    service = _FakeService(encoder_error=True)
    worker = _make_worker([CondenseItem(m1, s1), CondenseItem(m2, s2)], config, service=service)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert len(cap["finished"]) == 1
    idx, out, err = cap["finished"][0]
    assert idx == 0 and out is None
    assert "libmp3lame" in err
    assert 1 not in cap["started"]
    assert cap["queue_finished"] == [True]
    # Encoder-missing is a tool error, not a user cancel.
    assert worker.is_cancelled is False
    # condense attempted once (file 0), never for file 1.
    assert len(service.condense_calls) == 1


# ---------------------------------------------------------------------------
# Embedded-sub temp deleted in per-file finally (success + failure)
# ---------------------------------------------------------------------------


def _embedded_worker(config, media, service, monkeypatch):
    monkeypatch.setattr(cw, "find_sibling_subtitle", lambda *a, **k: None)
    monkeypatch.setattr(cw, "resolve_ffprobe", lambda config: "ffprobe")
    monkeypatch.setattr(cw, "list_subtitle_streams", lambda m, ffprobe: [_sub_stream(sub_index=0, codec="subrip")])
    return CondenseWorker(config, [CondenseItem(media)], service=service)


def test_embedded_temp_deleted_on_success(qapp, tmp_path, monkeypatch):
    """The extracted embedded-sub temp file is deleted after a successful condense."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")

    service = _FakeService()
    worker = _embedded_worker(config, media, service, monkeypatch)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert cap["finished"][0][2] is None
    assert service.extract_calls
    temp = service.extract_calls[0]["out_dir"] / f"{media.stem}.s0.srt"
    assert not temp.exists()


def test_embedded_temp_deleted_on_failure(qapp, tmp_path, monkeypatch):
    """The extracted embedded-sub temp file is deleted even when condense fails."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")

    service = _FakeService(condense_result=False)
    worker = _embedded_worker(config, media, service, monkeypatch)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert cap["finished"][0][1] is None  # failed
    temp = service.extract_calls[0]["out_dir"] / f"{media.stem}.s0.srt"
    assert not temp.exists()


# ---------------------------------------------------------------------------
# Condensed subtitle sidecars
# ---------------------------------------------------------------------------


def test_write_subs_writes_srt_and_lrc(qapp, tmp_path):
    """write_subs=True → both SRT and LRC sidecars written beside the audio."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "hello"), (5000, 6000, "world")])

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, sub)], config, service=service, write_subs=True)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert cap["finished"][0][2] is None
    srt = tmp_path / "ep01_condensed.srt"
    lrc = tmp_path / "ep01_condensed.lrc"
    assert srt.exists() and lrc.exists()
    # Sidecars carry the filtered/shifted dialogue only.
    reloaded = pysubs2.load(str(srt), format_="srt")
    texts = [e.text for e in reloaded]
    assert "hello" in texts and "world" in texts


def test_write_subs_uses_filtered_shifted_events(qapp, tmp_path):
    """Sidecars exclude filtered SFX lines and reflect the offset (D4)."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "dialogue"), (3000, 4000, "♪")])

    service = _FakeService()
    worker = _make_worker(
        [CondenseItem(media, sub)],
        config,
        service=service,
        write_subs=True,
        filtered_chars="♪",
    )
    worker.run()
    worker.wait(2000)

    reloaded = pysubs2.load(str(tmp_path / "ep01_condensed.srt"), format_="srt")
    texts = [e.text for e in reloaded]
    assert texts == ["dialogue"]  # SFX line dropped, only dialogue survives


def test_no_write_subs_when_disabled(qapp, tmp_path):
    """write_subs=False → no sidecars produced."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "hi")])

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, sub)], config, service=service, write_subs=False)
    worker.run()
    worker.wait(2000)

    assert not (tmp_path / "ep01_condensed.srt").exists()
    assert not (tmp_path / "ep01_condensed.lrc").exists()


def test_sub_write_failure_does_not_fail_audio(qapp, tmp_path, monkeypatch):
    """A sidecar write error is non-fatal: audio succeeds, warning surfaced in progress."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "hi")])

    def _boom(events, path):
        raise OSError("disk full")

    monkeypatch.setattr(cw, "write_condensed_srt", _boom)

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, sub)], config, service=service, write_subs=True)
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    idx, out, err = cap["finished"][0]
    assert out == tmp_path / "ep01_condensed.mp3"  # audio still succeeded
    assert err is None
    # Warning surfaced through the final progress message.
    final_msg = [p for p in cap["progress"] if p[0] == 0][-1][2]
    assert "disk full" in final_msg


# ---------------------------------------------------------------------------
# Miscellaneous contract checks
# ---------------------------------------------------------------------------


def test_queue_finished_on_empty_list(qapp, tmp_path):
    """queue_finished emitted even for an empty item list."""
    config = _make_config(tmp_path)
    worker = _make_worker([], config, service=_FakeService())
    cap = _capture(worker)
    worker.run()
    worker.wait(2000)

    assert cap["queue_finished"] == [True]
    assert cap["started"] == []
    assert cap["finished"] == []


def test_file_skipped_signal_exists(qapp, tmp_path):
    """CondenseWorker exposes a file_skipped(int, object) signal."""
    worker = _make_worker([], _make_config(tmp_path), service=_FakeService())
    assert hasattr(worker, "file_skipped")


def test_offset_shifts_periods(qapp, tmp_path):
    """offset_ms shifts every cue once before building periods."""
    config = _make_config(tmp_path)
    media = tmp_path / "ep01.mkv"
    media.write_bytes(b"")
    sub = _write_srt(tmp_path / "ep01.srt", [(1000, 2000, "hi")])

    service = _FakeService()
    worker = _make_worker([CondenseItem(media, sub)], config, service=service, offset_ms=1000, padding_ms=0)
    worker.run()
    worker.wait(2000)

    # 1000-2000 shifted +1000 → 2000-3000, no padding.
    assert service.condense_calls[0]["periods"] == [(2000, 3000)]

"""Tests for SubtitleGenWorker — signal sequence, skip/overwrite, error isolation, cancel, cleanup."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers.subtitle_gen_worker import SubtitleGenWorker

# Requires numpy (transitive asr dep via faster-whisper); gated to the asr CI job.
pytestmark = pytest.mark.asr

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FAKE_SEGMENTS = [(0.0, 1.0, "こんにちは"), (1.0, 2.0, "世界")]


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(
        asr_model="large-v3",
        asr_models_root=tmp_path / "models",
        media_temp_folder=tmp_path / "temp",
    )


class _FakeExtractor:
    """Minimal MediaExtractorService stand-in."""

    def __init__(self, *, fail: bool = False, create_wav: bool = True, tmp_path: Path | None = None) -> None:
        self._fail = fail
        self._create_wav = create_wav
        self._tmp_path = tmp_path
        self.calls: list[dict] = []

    def extract_full_audio(
        self,
        video_file: Path,
        out_wav: Path,
        *,
        track_override=None,
        cancel_event=None,
    ) -> bool:
        self.calls.append({"video_file": video_file, "out_wav": out_wav})
        if cancel_event is not None and cancel_event.is_set():
            return False
        if self._fail:
            return False
        if self._create_wav:
            out_wav.write_bytes(b"")
        return True


def _make_worker(
    video_files: list[Path],
    config: AnkiMinerConfig,
    *,
    extractor=None,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> SubtitleGenWorker:
    return SubtitleGenWorker(
        config,
        video_files,
        output_dir=output_dir,
        overwrite=overwrite,
        extractor=extractor,
    )


def _capture(worker: SubtitleGenWorker) -> dict:
    """Connect signal recorders and return the capture dict."""
    cap: dict = {
        "started": [],
        "progress": [],
        "finished": [],
        "queue_finished": [],
    }
    worker.file_started.connect(lambda idx: cap["started"].append(idx))
    worker.file_progress.connect(lambda idx, pct, msg: cap["progress"].append((idx, pct, msg)))
    worker.file_finished.connect(lambda idx, out, err: cap["finished"].append((idx, out, err)))
    worker.queue_finished.connect(lambda: cap["queue_finished"].append(True))
    return cap


# ---------------------------------------------------------------------------
# Monkeypatching helpers
# ---------------------------------------------------------------------------


def _patch_wav_to_float32(monkeypatch, *, audio=None, sample_rate=16000, duration_s=2.0):
    import numpy as np

    samples = audio if audio is not None else np.zeros(32000, dtype="float32")

    # Patch at the canonical location; the worker imports it from there at call time.
    import anki_miner.services.media_extractor as me

    monkeypatch.setattr(me, "wav_to_float32", lambda path: (samples, sample_rate, duration_s))


def _patch_transcribe(monkeypatch, *, segments=None, raise_exc=None):
    import anki_miner.services.asr.transcriber as t

    def _fake_transcribe(
        audio, *, model_name, models_root, sample_rate, duration_s, cancel_event=None, progress_cb=None
    ):
        if raise_exc is not None:
            raise raise_exc
        if progress_cb is not None:
            progress_cb(1.0)
        return segments if segments is not None else _FAKE_SEGMENTS

    monkeypatch.setattr(t, "transcribe", _fake_transcribe)


def _patch_srt_writer(monkeypatch, *, calls: list | None = None):
    import anki_miner.services.asr.srt_writer as sw

    written: list[tuple] = calls if calls is not None else []

    def _fake_write(segments, out_path):
        out_path.write_text("FAKE SRT")
        written.append((segments, out_path))

    monkeypatch.setattr(sw, "segments_to_srt", _fake_write)
    return written


# ---------------------------------------------------------------------------
# Happy-path: 2-file run
# ---------------------------------------------------------------------------


def test_happy_path_two_files_signal_sequence(qapp, tmp_path, monkeypatch):
    """2-file run: correct started/progress/finished per file + one queue_finished."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v1 = tmp_path / "ep01.mkv"
    v2 = tmp_path / "ep02.mkv"
    v1.write_bytes(b"")
    v2.write_bytes(b"")

    extractor = _FakeExtractor(tmp_path=tmp_path)
    srt_calls: list = []

    _patch_wav_to_float32(monkeypatch)
    _patch_transcribe(monkeypatch)
    _patch_srt_writer(monkeypatch, calls=srt_calls)

    worker = _make_worker([v1, v2], config, extractor=extractor)
    cap = _capture(worker)

    worker.run()

    # Both files started.
    assert cap["started"] == [0, 1]

    # Both files finished with out_path set and no error.
    assert len(cap["finished"]) == 2
    idx0, out0, err0 = cap["finished"][0]
    idx1, out1, err1 = cap["finished"][1]
    assert idx0 == 0 and out0 is not None and err0 is None
    assert idx1 == 1 and out1 is not None and err1 is None

    # queue_finished emitted exactly once.
    assert cap["queue_finished"] == [True]

    # Progress emitted for each file (at minimum the 100% "Done" marker).
    progresses_per_file = {idx: [p for p in cap["progress"] if p[0] == idx] for idx in (0, 1)}
    assert progresses_per_file[0][-1][1] == 100
    assert progresses_per_file[1][-1][1] == 100

    # SRT written for both files.
    assert len(srt_calls) == 2


def test_no_speech_reports_warning_and_writes_no_srt(qapp, tmp_path, monkeypatch):
    """Empty transcription surfaces a 'no speech' outcome, not a clean Done (C5)."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v = tmp_path / "silent.mkv"
    v.write_bytes(b"")

    extractor = _FakeExtractor(tmp_path=tmp_path)
    srt_calls: list = []

    _patch_wav_to_float32(monkeypatch)
    _patch_transcribe(monkeypatch, segments=[])
    _patch_srt_writer(monkeypatch, calls=srt_calls)

    worker = _make_worker([v], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    assert len(cap["finished"]) == 1
    _idx, out_path, err = cap["finished"][0]
    assert out_path is None
    assert err is not None and "No speech" in err
    assert srt_calls == []  # blank SRT must not be written


def test_cancel_during_transcribe_emits_cancelled(qapp, tmp_path, monkeypatch):
    """Cancel landing during transcription is caught post-transcribe, no SRT (T4)."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v = tmp_path / "ep.mkv"
    v.write_bytes(b"")

    extractor = _FakeExtractor(tmp_path=tmp_path)
    _patch_wav_to_float32(monkeypatch)
    srt_calls = _patch_srt_writer(monkeypatch)

    import anki_miner.services.asr.transcriber as t

    def _cancelling_transcribe(
        audio, *, model_name, models_root, sample_rate, duration_s, cancel_event=None, progress_cb=None
    ):
        if cancel_event is not None:
            cancel_event.set()  # user cancels mid-transcription
        return [(0.0, 1.0, "x")]

    monkeypatch.setattr(t, "transcribe", _cancelling_transcribe)

    worker = _make_worker([v], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    _idx, out_path, err = cap["finished"][0]
    assert out_path is None
    assert err == "Cancelled"
    assert srt_calls == []  # no SRT written after cancel


def test_srt_write_failure_reports_error_and_cleans_temp(qapp, tmp_path, monkeypatch):
    """A failure writing the SRT is forwarded as an error and the temp WAV removed (T4)."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v = tmp_path / "ep.mkv"
    v.write_bytes(b"")

    extractor = _FakeExtractor(tmp_path=tmp_path)
    _patch_wav_to_float32(monkeypatch)
    _patch_transcribe(monkeypatch)  # non-empty segments

    import anki_miner.services.asr.srt_writer as sw

    def _failing_write(segments, out_path):
        raise RuntimeError("disk full")

    monkeypatch.setattr(sw, "segments_to_srt", _failing_write)

    worker = _make_worker([v], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    _idx, out_path, err = cap["finished"][0]
    assert out_path is None
    assert "disk full" in err
    assert list(config.media_temp_folder.glob("asr_*.wav")) == []  # temp WAV cleaned


def test_happy_path_srt_written_next_to_source(qapp, tmp_path, monkeypatch):
    """Default output: SRT goes next to the source video."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v = tmp_path / "movie.mkv"
    v.write_bytes(b"")

    extractor = _FakeExtractor(tmp_path=tmp_path)
    srt_calls: list = []

    _patch_wav_to_float32(monkeypatch)
    _patch_transcribe(monkeypatch)
    _patch_srt_writer(monkeypatch, calls=srt_calls)

    worker = _make_worker([v], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    assert cap["finished"][0][1] == tmp_path / "movie.srt"


def test_happy_path_srt_written_to_custom_dir(qapp, tmp_path, monkeypatch):
    """Custom output_dir: SRT goes to the given directory."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v = tmp_path / "ep01.mkv"
    v.write_bytes(b"")
    out_dir = tmp_path / "subs"

    extractor = _FakeExtractor(tmp_path=tmp_path)
    srt_calls: list = []

    _patch_wav_to_float32(monkeypatch)
    _patch_transcribe(monkeypatch)
    _patch_srt_writer(monkeypatch, calls=srt_calls)

    worker = _make_worker([v], config, extractor=extractor, output_dir=out_dir)
    cap = _capture(worker)
    worker.run()

    expected = out_dir / "ep01.srt"
    assert cap["finished"][0][1] == expected


# ---------------------------------------------------------------------------
# Skip-if-exists vs overwrite
# ---------------------------------------------------------------------------


def test_skip_if_exists_no_overwrite(qapp, tmp_path, monkeypatch):
    """Existing SRT → file_finished with the existing path, no transcription."""
    config = _make_config(tmp_path)
    existing_srt = tmp_path / "ep01.srt"
    existing_srt.write_text("OLD SRT")

    v = tmp_path / "ep01.mkv"
    v.write_bytes(b"")

    extractor = _FakeExtractor(tmp_path=tmp_path)

    transcribe_calls: list = []

    import anki_miner.services.asr.transcriber as t

    def _no_transcribe(*a, **kw):
        transcribe_calls.append(1)
        return []

    monkeypatch.setattr(t, "transcribe", _no_transcribe)

    worker = _make_worker([v], config, extractor=extractor, overwrite=False)
    cap = _capture(worker)
    worker.run()

    assert cap["started"] == [0]
    assert cap["finished"] == [(0, existing_srt, None)]
    assert cap["queue_finished"] == [True]
    # Transcription must NOT have been called.
    assert transcribe_calls == []
    # Extractor must NOT have been called.
    assert extractor.calls == []


def test_overwrite_re_transcribes_existing(qapp, tmp_path, monkeypatch):
    """overwrite=True → existing SRT is re-generated (transcriber is called)."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    existing_srt = tmp_path / "ep01.srt"
    existing_srt.write_text("OLD SRT")

    v = tmp_path / "ep01.mkv"
    v.write_bytes(b"")

    extractor = _FakeExtractor(tmp_path=tmp_path)
    transcribe_calls: list = []

    def _fake_transcribe(
        audio, *, model_name, models_root, sample_rate, duration_s, cancel_event=None, progress_cb=None
    ):
        transcribe_calls.append(1)
        if progress_cb is not None:
            progress_cb(1.0)
        return _FAKE_SEGMENTS

    import anki_miner.services.asr.srt_writer as sw
    import anki_miner.services.asr.transcriber as t

    monkeypatch.setattr(t, "transcribe", _fake_transcribe)
    monkeypatch.setattr(sw, "segments_to_srt", lambda segs, p: p.write_text("NEW SRT"))
    _patch_wav_to_float32(monkeypatch)

    worker = _make_worker([v], config, extractor=extractor, overwrite=True)
    cap = _capture(worker)
    worker.run()

    assert transcribe_calls == [1]
    idx, out, err = cap["finished"][0]
    assert err is None
    assert out == tmp_path / "ep01.srt"


# ---------------------------------------------------------------------------
# Per-file error isolation
# ---------------------------------------------------------------------------


def test_per_file_error_isolation(qapp, tmp_path, monkeypatch):
    """File 1 raises → file_finished carries error; file 2 still runs and succeeds."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v1 = tmp_path / "ep01.mkv"
    v2 = tmp_path / "ep02.mkv"
    v1.write_bytes(b"")
    v2.write_bytes(b"")

    call_count = [0]

    class _SelectiveFakeExtractor:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def extract_full_audio(self, video_file, out_wav, *, track_override=None, cancel_event=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("extraction boom")
            out_wav.write_bytes(b"")
            return True

    extractor = _SelectiveFakeExtractor()
    _patch_wav_to_float32(monkeypatch)
    _patch_transcribe(monkeypatch)
    _patch_srt_writer(monkeypatch)

    worker = _make_worker([v1, v2], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    # Both files started.
    assert cap["started"] == [0, 1]

    # File 0 has error, file 1 succeeded.
    finished_map = {item[0]: item for item in cap["finished"]}
    assert finished_map[0][1] is None  # no out_path
    assert "extraction boom" in finished_map[0][2]  # error msg
    assert finished_map[1][1] is not None  # out_path set
    assert finished_map[1][2] is None  # no error

    # queue_finished still emitted.
    assert cap["queue_finished"] == [True]


def test_extraction_returning_false_is_treated_as_error(qapp, tmp_path, monkeypatch):
    """extract_full_audio returning False emits file_finished with an error string."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v = tmp_path / "ep01.mkv"
    v.write_bytes(b"")

    extractor = _FakeExtractor(fail=True)

    worker = _make_worker([v], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    assert cap["finished"][0][1] is None
    assert cap["finished"][0][2] is not None  # error string
    assert cap["queue_finished"] == [True]


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_before_run_emits_queue_finished_immediately(qapp, tmp_path, monkeypatch):
    """cancel() before run() stops the loop before processing any file."""
    config = _make_config(tmp_path)

    v1 = tmp_path / "ep01.mkv"
    v2 = tmp_path / "ep02.mkv"
    v1.write_bytes(b"")
    v2.write_bytes(b"")

    extractor = _FakeExtractor(tmp_path=tmp_path)

    worker = _make_worker([v1, v2], config, extractor=extractor)
    cap = _capture(worker)
    worker.cancel()
    worker.run()

    assert cap["started"] == []
    assert cap["finished"] == []
    assert cap["queue_finished"] == [True]
    assert extractor.calls == []


def test_cancel_between_files(qapp, tmp_path, monkeypatch):
    """cancel() during first file causes the second file to be skipped."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v1 = tmp_path / "ep01.mkv"
    v2 = tmp_path / "ep02.mkv"
    v1.write_bytes(b"")
    v2.write_bytes(b"")

    cancel_event_ref: list = []

    class _CancellingExtractor:
        def extract_full_audio(self, video_file, out_wav, *, track_override=None, cancel_event=None):
            cancel_event_ref.append(cancel_event)
            out_wav.write_bytes(b"")
            # Cancel after processing the first file's extraction.
            if cancel_event is not None:
                cancel_event.set()
            return True

    extractor = _CancellingExtractor()
    _patch_wav_to_float32(monkeypatch)
    _patch_transcribe(monkeypatch)
    _patch_srt_writer(monkeypatch)

    worker = _make_worker([v1, v2], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    # First file started.
    assert 0 in cap["started"]
    # Second file must NOT have started (cancel set mid-first-file).
    assert 1 not in cap["started"]
    # File 0 must have finished with a Cancelled error (no out_path).
    finished_map = {item[0]: item for item in cap["finished"]}
    assert 0 in finished_map, "file_finished was not emitted for file 0"
    assert finished_map[0][1] is None  # no out_path on cancel
    assert "Cancelled" in (finished_map[0][2] or "")
    # queue_finished still emitted.
    assert cap["queue_finished"] == [True]


# ---------------------------------------------------------------------------
# Temp WAV cleanup
# ---------------------------------------------------------------------------


def test_temp_wav_deleted_on_success(qapp, tmp_path, monkeypatch):
    """The temp WAV file is deleted after successful transcription."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v = tmp_path / "ep01.mkv"
    v.write_bytes(b"")

    created_wavs: list[Path] = []

    class _TrackingExtractor:
        def extract_full_audio(self, video_file, out_wav, *, track_override=None, cancel_event=None):
            out_wav.write_bytes(b"")
            created_wavs.append(out_wav)
            return True

    extractor = _TrackingExtractor()
    _patch_wav_to_float32(monkeypatch)
    _patch_transcribe(monkeypatch)
    _patch_srt_writer(monkeypatch)

    worker = _make_worker([v], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    assert cap["finished"][0][2] is None  # success
    assert created_wavs, "Extractor was never called"
    for wav in created_wavs:
        assert not wav.exists(), f"Temp WAV not deleted: {wav}"


def test_temp_wav_deleted_on_failure(qapp, tmp_path, monkeypatch):
    """The temp WAV file is deleted even when transcription raises."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v = tmp_path / "ep01.mkv"
    v.write_bytes(b"")

    created_wavs: list[Path] = []

    class _TrackingExtractor:
        def extract_full_audio(self, video_file, out_wav, *, track_override=None, cancel_event=None):
            out_wav.write_bytes(b"")
            created_wavs.append(out_wav)
            return True

    extractor = _TrackingExtractor()
    _patch_wav_to_float32(monkeypatch)
    _patch_transcribe(monkeypatch, raise_exc=RuntimeError("transcribe boom"))

    worker = _make_worker([v], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    assert "transcribe boom" in (cap["finished"][0][2] or "")
    assert created_wavs, "Extractor was never called"
    for wav in created_wavs:
        assert not wav.exists(), f"Temp WAV not deleted on failure: {wav}"


# ---------------------------------------------------------------------------
# queue_finished always emitted
# ---------------------------------------------------------------------------


def test_queue_finished_emitted_on_empty_list(qapp, tmp_path):
    """queue_finished is emitted even for an empty file list."""
    config = _make_config(tmp_path)
    extractor = _FakeExtractor()
    worker = _make_worker([], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    assert cap["queue_finished"] == [True]
    assert cap["started"] == []
    assert cap["finished"] == []


# ---------------------------------------------------------------------------
# Extractor cancel_event is the worker's _cancel_event
# ---------------------------------------------------------------------------


def test_cancel_event_forwarded_to_extractor(qapp, tmp_path, monkeypatch):
    """The worker's _cancel_event is passed to extract_full_audio."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v = tmp_path / "ep01.mkv"
    v.write_bytes(b"")

    received_events: list[object] = []

    class _EventCapturingExtractor:
        def extract_full_audio(self, video_file, out_wav, *, track_override=None, cancel_event=None):
            received_events.append(cancel_event)
            out_wav.write_bytes(b"")
            return True

    extractor = _EventCapturingExtractor()
    _patch_wav_to_float32(monkeypatch)
    _patch_transcribe(monkeypatch)
    _patch_srt_writer(monkeypatch)

    worker = _make_worker([v], config, extractor=extractor)
    _capture(worker)
    worker.run()

    assert len(received_events) == 1
    assert received_events[0] is worker._cancel_event


# ---------------------------------------------------------------------------
# Force 100% on success
# ---------------------------------------------------------------------------


def test_final_progress_is_100_on_success(qapp, tmp_path, monkeypatch):
    """file_progress(idx, 100, ...) is emitted as the last progress before file_finished."""
    config = _make_config(tmp_path)
    config.media_temp_folder.mkdir(parents=True, exist_ok=True)

    v = tmp_path / "ep01.mkv"
    v.write_bytes(b"")

    extractor = _FakeExtractor(tmp_path=tmp_path)

    def _partial_progress_transcribe(
        audio, *, model_name, models_root, sample_rate, duration_s, cancel_event=None, progress_cb=None
    ):
        # Only emit 50%, not 100% — worker must force 100%.
        if progress_cb is not None:
            progress_cb(0.5)
        return _FAKE_SEGMENTS

    import anki_miner.services.asr.srt_writer as sw
    import anki_miner.services.asr.transcriber as t

    monkeypatch.setattr(t, "transcribe", _partial_progress_transcribe)
    monkeypatch.setattr(sw, "segments_to_srt", lambda segs, p: p.write_text("SRT"))
    _patch_wav_to_float32(monkeypatch)

    worker = _make_worker([v], config, extractor=extractor)
    cap = _capture(worker)
    worker.run()

    file_progresses = [p for p in cap["progress"] if p[0] == 0]
    final_pct = file_progresses[-1][1]
    assert final_pct == 100, f"Expected final progress 100, got {final_pct}"

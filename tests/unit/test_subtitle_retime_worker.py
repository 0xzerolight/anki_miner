"""Tests for SubtitleRetimeWorker — signal contract, output path, skip/overwrite,
success/failure, AlassNotFoundError queue-stop, cancel, and log_cb forwarding."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.gui.workers.subtitle_retime_worker import SubtitleRetimeWorker

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_config() -> AnkiMinerConfig:
    return AnkiMinerConfig()


def _make_worker(
    pairs: list[tuple[Path, Path]],
    config: AnkiMinerConfig | None = None,
    *,
    output_dir: Path | None = None,
    overwrite: bool = False,
    split_penalty: float = 7,
    retimer=None,
) -> SubtitleRetimeWorker:
    if config is None:
        config = _make_config()
    return SubtitleRetimeWorker(
        config,
        pairs,
        output_dir=output_dir,
        overwrite=overwrite,
        split_penalty=split_penalty,
        retimer=retimer,
    )


def _capture(worker: SubtitleRetimeWorker) -> dict:
    """Connect signal recorders and return the capture dict."""
    cap: dict = {
        "started": [],
        "progress": [],
        "finished": [],
        "skipped": [],
        "queue_finished": [],
    }
    worker.file_started.connect(lambda idx: cap["started"].append(idx))
    worker.file_progress.connect(lambda idx, pct, msg: cap["progress"].append((idx, pct, msg)))
    worker.file_finished.connect(lambda idx, out, err: cap["finished"].append((idx, out, err)))
    worker.file_skipped.connect(lambda idx, out: cap["skipped"].append((idx, out)))
    worker.queue_finished.connect(lambda: cap["queue_finished"].append(True))
    return cap


def _fake_retimer_success(*args, cancel_event=None, log_cb=None, **kwargs):
    """Fake retimer that always returns True (success)."""
    return True


def _fake_retimer_failure(*args, cancel_event=None, log_cb=None, **kwargs):
    """Fake retimer that always returns False (failure, not cancelled)."""
    return False


# ---------------------------------------------------------------------------
# Signal contract: file_started / file_finished per pair, queue_finished once
# ---------------------------------------------------------------------------


def test_forwards_alass_options_to_retimer(qapp, tmp_path):
    """Worker forwards disable_fps_guessing / no_split / audio_track_override."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    for p in (v, s):
        p.write_bytes(b"")

    captured: list[dict] = []

    def _recording_retimer(*args, **kwargs):
        captured.append(kwargs)
        return True

    worker = SubtitleRetimeWorker(
        _make_config(),
        [(v, s)],
        split_penalty=12.0,
        disable_fps_guessing=False,
        no_split=True,
        audio_track_override=3,
        retimer=_recording_retimer,
    )
    worker.run()

    assert len(captured) == 1
    kw = captured[0]
    assert kw["split_penalty"] == 12.0
    assert kw["disable_fps_guessing"] is False
    assert kw["no_split"] is True
    assert kw["audio_track_override"] == 3


def test_default_alass_options_forwarded(qapp, tmp_path):
    """Defaults: fps-guessing disabled, no split, auto-detect track (None)."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    for p in (v, s):
        p.write_bytes(b"")

    captured: list[dict] = []

    def _recording_retimer(*args, **kwargs):
        captured.append(kwargs)
        return True

    worker = _make_worker([(v, s)], retimer=_recording_retimer)
    worker.run()

    kw = captured[0]
    assert kw["disable_fps_guessing"] is True
    assert kw["no_split"] is False
    assert kw["audio_track_override"] is None


def test_signal_contract_two_pairs(qapp, tmp_path):
    """2-pair run: correct started/finished per pair + one queue_finished."""
    v1 = tmp_path / "ep01.mkv"
    v2 = tmp_path / "ep02.mkv"
    s1 = tmp_path / "ep01_orig.srt"
    s2 = tmp_path / "ep02_orig.srt"
    for p in (v1, v2, s1, s2):
        p.write_bytes(b"")

    worker = _make_worker([(v1, s1), (v2, s2)], retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    assert cap["started"] == [0, 1]
    assert len(cap["finished"]) == 2
    assert cap["finished"][0][0] == 0
    assert cap["finished"][1][0] == 1
    assert cap["queue_finished"] == [True]


def test_queue_finished_on_empty_list(qapp, tmp_path):
    """queue_finished is emitted even for an empty pairs list."""
    worker = _make_worker([], retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    assert cap["queue_finished"] == [True]
    assert cap["started"] == []
    assert cap["finished"] == []


# ---------------------------------------------------------------------------
# Output path
# ---------------------------------------------------------------------------


def test_output_path_next_to_video(qapp, tmp_path):
    """Default: output uses video.stem + sub.suffix, placed next to video."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "whatever.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    worker = _make_worker([(v, s)], retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    expected = tmp_path / "ep01.srt"
    assert cap["finished"][0][1] == expected


def test_output_path_in_output_dir(qapp, tmp_path):
    """output_dir set: output placed in that directory with video.stem + sub.suffix."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "whatever.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")
    out_dir = tmp_path / "retimed"

    worker = _make_worker([(v, s)], output_dir=out_dir, retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    expected = out_dir / "ep01.srt"
    assert cap["finished"][0][1] == expected


def test_output_dir_is_created(qapp, tmp_path):
    """output_dir is created when it does not exist yet."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")
    out_dir = tmp_path / "new" / "nested" / "dir"

    assert not out_dir.exists()

    worker = _make_worker([(v, s)], output_dir=out_dir, retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    assert out_dir.exists()
    assert cap["finished"][0][2] is None  # success


def test_output_path_preserves_subtitle_extension(qapp, tmp_path):
    """Subtitle extension (.ass) is preserved in the output filename."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.ass"
    v.write_bytes(b"")
    s.write_bytes(b"")

    worker = _make_worker([(v, s)], retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    expected = tmp_path / "ep01.ass"
    assert cap["finished"][0][1] == expected


# ---------------------------------------------------------------------------
# Skip-if-exists vs overwrite
# ---------------------------------------------------------------------------


def test_skip_if_exists_no_overwrite(qapp, tmp_path):
    """Existing output → file_skipped emitted, file_finished NOT emitted, retimer NOT called."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    out = tmp_path / "ep01.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")
    out.write_text("OLD SRT")

    retimer_calls: list = []

    def _recording_retimer(*args, cancel_event=None, log_cb=None, **kwargs):
        retimer_calls.append(1)
        return True

    worker = _make_worker([(v, s)], overwrite=False, retimer=_recording_retimer)
    cap = _capture(worker)
    worker.run()

    # Skip must emit file_skipped, NOT file_finished.
    assert cap["skipped"] == [(0, out)]
    assert cap["finished"] == []
    assert cap["queue_finished"] == [True]
    # Retimer must NOT have been called.
    assert retimer_calls == []
    # "Skipped, exists" progress must still be emitted.
    skipped_progress = [p for p in cap["progress"] if p[0] == 0 and p[1] == 100]
    assert any("Skipped" in p[2] for p in skipped_progress)


def test_overwrite_calls_retimer_on_existing(qapp, tmp_path):
    """overwrite=True → retimer is called even when output already exists."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    out = tmp_path / "ep01.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")
    out.write_text("OLD SRT")

    retimer_calls: list = []

    def _recording_retimer(*args, cancel_event=None, log_cb=None, **kwargs):
        retimer_calls.append(1)
        return True

    worker = _make_worker([(v, s)], overwrite=True, retimer=_recording_retimer)
    cap = _capture(worker)
    worker.run()

    assert retimer_calls == [1]
    idx, out_path, err = cap["finished"][0]
    assert err is None
    assert out_path == out


# ---------------------------------------------------------------------------
# Success and failure
# ---------------------------------------------------------------------------


def test_success_emits_out_path_no_error(qapp, tmp_path):
    """Retimer returns True → file_finished(idx, out_sub, None)."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    worker = _make_worker([(v, s)], retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    idx, out_path, err = cap["finished"][0]
    assert out_path == tmp_path / "ep01.srt"
    assert err is None


def test_failure_emits_none_out_and_error(qapp, tmp_path):
    """Retimer returns False (not cancelled) → file_finished(idx, None, error_msg)."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    worker = _make_worker([(v, s)], retimer=_fake_retimer_failure)
    cap = _capture(worker)
    worker.run()

    idx, out_path, err = cap["finished"][0]
    assert out_path is None
    assert err is not None
    assert v.name in err  # spec message: "Retiming failed for <name>"


def test_success_emits_100_progress(qapp, tmp_path):
    """Successful retiming emits file_progress(idx, 100, ...) as final progress."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    worker = _make_worker([(v, s)], retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    file_progresses = [p for p in cap["progress"] if p[0] == 0]
    assert file_progresses[-1][1] == 100


# ---------------------------------------------------------------------------
# AlassNotFoundError: stops queue, queue_finished still fires
# ---------------------------------------------------------------------------


def test_alass_not_found_stops_queue(qapp, tmp_path):
    """AlassNotFoundError on pair 0 → that pair errors, pair 1 NOT processed; queue_finished fires."""
    v1 = tmp_path / "ep01.mkv"
    v2 = tmp_path / "ep02.mkv"
    s1 = tmp_path / "ep01_orig.srt"
    s2 = tmp_path / "ep02_orig.srt"
    for p in (v1, v2, s1, s2):
        p.write_bytes(b"")

    def _alass_missing(*args, cancel_event=None, log_cb=None, **kwargs):
        raise AlassNotFoundError("alass binary not found: 'alass'")

    worker = _make_worker([(v1, s1), (v2, s2)], retimer=_alass_missing)
    cap = _capture(worker)
    worker.run()

    # Pair 0 must have an error.
    assert len(cap["finished"]) == 1
    idx, out_path, err = cap["finished"][0]
    assert idx == 0
    assert out_path is None
    assert "alass" in err.lower()

    # Pair 1 must NOT have been started.
    assert 1 not in cap["started"]

    # queue_finished must still fire.
    assert cap["queue_finished"] == [True]

    # is_cancelled must stay False — alass-missing is a tool error, not a user
    # cancel; callers rely on this to distinguish the two.
    assert worker.is_cancelled is False


# ---------------------------------------------------------------------------
# Per-pair error isolation (unexpected exception)
# ---------------------------------------------------------------------------


def test_unexpected_exception_isolated_per_pair(qapp, tmp_path):
    """Unexpected exception on pair 0 → error forwarded; pair 1 still runs."""
    v1 = tmp_path / "ep01.mkv"
    v2 = tmp_path / "ep02.mkv"
    s1 = tmp_path / "ep01_orig.srt"
    s2 = tmp_path / "ep02_orig.srt"
    for p in (v1, v2, s1, s2):
        p.write_bytes(b"")

    call_count = [0]

    def _boom_then_ok(*args, cancel_event=None, log_cb=None, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("unexpected boom")
        return True

    worker = _make_worker([(v1, s1), (v2, s2)], retimer=_boom_then_ok)
    cap = _capture(worker)
    worker.run()

    assert cap["started"] == [0, 1]

    finished_map = {item[0]: item for item in cap["finished"]}
    assert finished_map[0][1] is None  # no out_path
    assert "unexpected boom" in finished_map[0][2]
    assert finished_map[1][1] is not None  # success
    assert finished_map[1][2] is None

    assert cap["queue_finished"] == [True]


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_before_run_skips_all(qapp, tmp_path):
    """cancel() before run() — loop exits immediately; queue_finished fires."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    retimer_calls: list = []

    def _recording_retimer(*args, cancel_event=None, log_cb=None, **kwargs):
        retimer_calls.append(1)
        return True

    worker = _make_worker([(v, s)], retimer=_recording_retimer)
    cap = _capture(worker)
    worker.cancel()
    worker.run()

    assert cap["started"] == []
    assert cap["finished"] == []
    assert cap["queue_finished"] == [True]
    assert retimer_calls == []


def test_cancel_via_retimer_reports_cancelled(qapp, tmp_path):
    """Retimer sets cancel_event and returns False → file_finished reports 'Cancelled'."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    def _cancelling_retimer(*args, cancel_event=None, log_cb=None, **kwargs):
        if cancel_event is not None:
            cancel_event.set()
        return False

    worker = _make_worker([(v, s)], retimer=_cancelling_retimer)
    cap = _capture(worker)
    worker.run()

    idx, out_path, err = cap["finished"][0]
    assert out_path is None
    assert err == "Cancelled"


def test_cancel_between_pairs(qapp, tmp_path):
    """Cancel during pair 0 retiming → pair 1 is skipped."""
    v1 = tmp_path / "ep01.mkv"
    v2 = tmp_path / "ep02.mkv"
    s1 = tmp_path / "ep01_orig.srt"
    s2 = tmp_path / "ep02_orig.srt"
    for p in (v1, v2, s1, s2):
        p.write_bytes(b"")

    call_count = [0]

    def _cancel_on_first(*args, cancel_event=None, log_cb=None, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1 and cancel_event is not None:
            cancel_event.set()
        return False  # cancelled → False

    worker = _make_worker([(v1, s1), (v2, s2)], retimer=_cancel_on_first)
    cap = _capture(worker)
    worker.run()

    assert 0 in cap["started"]
    assert 1 not in cap["started"]

    finished_map = {item[0]: item for item in cap["finished"]}
    assert finished_map[0][1] is None
    assert finished_map[0][2] == "Cancelled"

    assert cap["queue_finished"] == [True]


# ---------------------------------------------------------------------------
# log_cb forwarding
# ---------------------------------------------------------------------------


def test_log_cb_forwarded_as_file_progress(qapp, tmp_path):
    """Alass lines emitted via log_cb arrive as file_progress signals."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    def _logging_retimer(*args, cancel_event=None, log_cb=None, **kwargs):
        if log_cb is not None:
            log_cb("progress: 42%")
            log_cb("progress: 84%")
        return True

    worker = _make_worker([(v, s)], retimer=_logging_retimer)
    cap = _capture(worker)
    worker.run()

    in_progress_msgs = [p[2] for p in cap["progress"] if p[0] == 0 and p[1] == 0]
    assert "progress: 42%" in in_progress_msgs
    assert "progress: 84%" in in_progress_msgs


def test_log_cb_pct_is_zero_for_alass_lines(qapp, tmp_path):
    """Alass log lines are forwarded with pct=0 (indeterminate)."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    def _logging_retimer(*args, cancel_event=None, log_cb=None, **kwargs):
        if log_cb is not None:
            log_cb("some alass output")
        return True

    worker = _make_worker([(v, s)], retimer=_logging_retimer)
    cap = _capture(worker)
    worker.run()

    alass_progress = [p for p in cap["progress"] if p[0] == 0 and p[2] == "some alass output"]
    assert len(alass_progress) == 1
    assert alass_progress[0][1] == 0


# ---------------------------------------------------------------------------
# split_penalty forwarded to retimer
# ---------------------------------------------------------------------------


def test_split_penalty_forwarded_to_retimer(qapp, tmp_path):
    """split_penalty constructor arg is passed to the retimer."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    received_penalty: list = []

    def _recording_retimer(*args, split_penalty=7, cancel_event=None, log_cb=None, **kwargs):
        received_penalty.append(split_penalty)
        return True

    worker = _make_worker([(v, s)], split_penalty=42.0, retimer=_recording_retimer)
    _capture(worker)
    worker.run()

    assert received_penalty == [42.0]


# ---------------------------------------------------------------------------
# file_skipped signal exists on the worker
# ---------------------------------------------------------------------------


def test_file_skipped_signal_exists(qapp, tmp_path):
    """SubtitleRetimeWorker exposes a file_skipped(int, object) signal."""
    worker = _make_worker([], retimer=_fake_retimer_success)
    assert hasattr(worker, "file_skipped")

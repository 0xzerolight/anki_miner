"""Tests for SubtitleRetimeWorker — signal contract, output path, skip/overwrite,
success/failure, per-pair error isolation, cancel, and log_cb forwarding."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.gui.workers.subtitle_retime_worker import SubtitleRetimeWorker
from anki_miner.services.retime_reference import ReferenceOverride
from anki_miner.services.subtitle_retimer import RetimeOutcome

# A dakuten kana stem that genuinely decomposes under NFD (common kana like ねこ
# are byte-identical NFC/NFD and would be a false-green for the dedup tests).
_DECOMP_STEM = "が01"
_NFC = unicodedata.normalize("NFC", _DECOMP_STEM)
_NFD = unicodedata.normalize("NFD", _DECOMP_STEM)


def _nfc_stem_srt_count(folder: Path, stem: str) -> int:
    """Count .srt files in *folder* whose stem NFC-normalizes to *stem* (NFC)."""
    target = unicodedata.normalize("NFC", stem)
    return sum(1 for p in folder.iterdir() if p.suffix == ".srt" and unicodedata.normalize("NFC", p.stem) == target)


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
    retimer=None,
) -> SubtitleRetimeWorker:
    if config is None:
        config = _make_config()
    return SubtitleRetimeWorker(
        config,
        pairs,
        output_dir=output_dir,
        overwrite=overwrite,
        retimer=retimer,
    )


def _capture(worker: SubtitleRetimeWorker) -> dict:
    """Connect signal recorders and return the capture dict."""
    cap: dict = {
        "started": [],
        "progress": [],
        "finished": [],
        "notes": [],
        "skipped": [],
        "queue_finished": [],
    }
    worker.file_started.connect(lambda idx: cap["started"].append(idx))
    worker.file_progress.connect(lambda idx, pct, msg: cap["progress"].append((idx, pct, msg)))
    worker.file_finished.connect(lambda idx, out, err: cap["finished"].append((idx, out, err)))
    worker.file_note.connect(lambda idx, note: cap["notes"].append((idx, note)))
    worker.file_skipped.connect(lambda idx, out, reason: cap["skipped"].append((idx, out, reason)))
    worker.queue_finished.connect(lambda _outcome: cap["queue_finished"].append(True))
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


def test_retimer_receives_only_pipeline_kwargs(qapp, tmp_path):
    """No per-run tuning knobs: the retimer gets the override, cancel event and
    log callback — alignment decisions belong to the pipeline, not config."""
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
        reference_override=ReferenceOverride(kind="audio", index=3),
        retimer=_recording_retimer,
    )
    worker.run()

    assert len(captured) == 1
    kw = captured[0]
    assert kw["reference_override"] == ReferenceOverride(kind="audio", index=3)
    assert set(kw) == {"reference_override", "cancel_event", "log_cb"}


def test_default_reference_override_is_none(qapp, tmp_path):
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

    assert captured[0]["reference_override"] is None


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
    """Default: output uses in_sub.stem + _retimed + sub.suffix, next to video."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "whatever.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    worker = _make_worker([(v, s)], retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    expected = tmp_path / "whatever_retimed.srt"
    assert cap["finished"][0][1] == expected


def test_output_path_in_output_dir(qapp, tmp_path):
    """output_dir set: the _retimed output is placed in that directory."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "whatever.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")
    out_dir = tmp_path / "retimed"

    worker = _make_worker([(v, s)], output_dir=out_dir, retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    expected = out_dir / "whatever_retimed.srt"
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


def test_run_envelope_turns_mkdir_error_into_terminal_result(qapp, tmp_path, monkeypatch):
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")
    out_dir = tmp_path / "blocked"
    retimer = MagicMock(return_value=True)
    worker = _make_worker([(v, s)], output_dir=out_dir, retimer=retimer)
    cap = _capture(worker)
    monkeypatch.setattr(Path, "mkdir", MagicMock(side_effect=PermissionError("mkdir denied")))

    worker.run()

    assert cap["started"] == [0]
    assert cap["finished"] == [(0, None, "mkdir denied")]
    assert cap["queue_finished"] == [True]
    retimer.assert_not_called()


def test_output_path_preserves_subtitle_extension(qapp, tmp_path):
    """Subtitle extension (.ass) is preserved in the output filename."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.ass"
    v.write_bytes(b"")
    s.write_bytes(b"")

    worker = _make_worker([(v, s)], retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    expected = tmp_path / "ep01_orig_retimed.ass"
    assert cap["finished"][0][1] == expected


# ---------------------------------------------------------------------------
# Skip-if-exists vs overwrite
# ---------------------------------------------------------------------------


def test_skip_if_exists_no_overwrite(qapp, tmp_path):
    """Existing output → file_skipped emitted, file_finished NOT emitted, retimer NOT called."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    out = tmp_path / "ep01_orig_retimed.srt"
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

    # Skip must emit file_skipped (with the reason), NOT file_finished.
    assert cap["skipped"] == [(0, out, "Skipped, exists")]
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
    out = tmp_path / "ep01_orig_retimed.srt"
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


def test_sub_named_after_video_is_not_overwritten(qapp, tmp_path):
    """A subtitle already named ``<video stem><suffix>`` used to resolve onto
    itself, so a run overwrote the user's own file. The _retimed suffix makes
    that impossible: the run proceeds (no skip) and writes a sibling."""
    v = tmp_path / "ep01.mkv"
    in_sub = tmp_path / "ep01.srt"
    v.write_bytes(b"")
    in_sub.write_text("SUB")

    captured: list[Path] = []
    worker = _make_worker([(v, in_sub)], overwrite=False, retimer=_make_writing_retimer(captured))
    cap = _capture(worker)
    worker.run()

    assert captured == [tmp_path / "ep01_retimed.srt"]
    assert cap["skipped"] == []
    assert cap["finished"][0][1] == tmp_path / "ep01_retimed.srt"
    assert in_sub.read_text() == "SUB"  # the input is never the target


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
    assert out_path == tmp_path / "ep01_orig_retimed.srt"
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
# file_note: the durable per-file record (C-7/C-10) — separate from the
# transient file_progress label, which the tab overwrites on the next update.
# ---------------------------------------------------------------------------


def test_success_emits_file_note_naming_the_engine(qapp, tmp_path):
    """A real RetimeOutcome's engine reaches file_note, not just file_progress."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    def _retimer(*args, cancel_event=None, log_cb=None, **kwargs):
        return RetimeOutcome(ok=True, engine="ffsubsync (single offset)")

    worker = _make_worker([(v, s)], retimer=_retimer)
    cap = _capture(worker)
    worker.run()

    notes = [note for idx, note in cap["notes"] if idx == 0]
    assert any("ffsubsync (single offset)" in note for note in notes)
    # Every note precedes the pair's file_finished — the durable line is
    # readable in the log before "Done: <name>" appears underneath it.
    assert len(cap["finished"]) == 1


def test_success_without_engine_emits_no_engine_note(qapp, tmp_path):
    """A bare bool (most test doubles, and any non-RetimeOutcome retimer) has
    no engine to report — file_note must not fire a blank/garbage line."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")

    worker = _make_worker([(v, s)], retimer=_fake_retimer_success)
    cap = _capture(worker)
    worker.run()

    assert cap["notes"] == []


def test_overwrite_writes_no_backup_and_notes_none(qapp, tmp_path):
    """Overwriting a previous _retimed output leaves no .bak sibling behind and
    reports no backup: the file being replaced is regenerable, and the user's
    own subtitle was never the target."""
    v = tmp_path / "ep01.mkv"
    s = tmp_path / "ep01_orig.srt"
    out = tmp_path / "ep01_orig_retimed.srt"
    v.write_bytes(b"")
    s.write_bytes(b"")
    out.write_text("OLD SRT")

    def _retimer(*args, cancel_event=None, log_cb=None, **kwargs):
        return RetimeOutcome(ok=True, engine="ffsubsync")

    worker = _make_worker([(v, s)], overwrite=True, retimer=_retimer)
    cap = _capture(worker)
    worker.run()

    notes = [note for idx, note in cap["notes"] if idx == 0]
    assert not any("backed up" in note for note in notes)
    assert list(tmp_path.glob("*.bak")) == []


# ---------------------------------------------------------------------------
# AlassNotFoundError: no longer fatal — the pipeline has other engines
# ---------------------------------------------------------------------------


def test_alass_not_found_is_isolated_per_pair(qapp, tmp_path):
    """A leaked AlassNotFoundError errors that pair only; the queue continues.

    The retime pipeline normally absorbs a missing alass (ffsubsync still
    runs), so the worker no longer treats it as queue-fatal.
    """
    v1 = tmp_path / "ep01.mkv"
    v2 = tmp_path / "ep02.mkv"
    s1 = tmp_path / "ep01_orig.srt"
    s2 = tmp_path / "ep02_orig.srt"
    for p in (v1, v2, s1, s2):
        p.write_bytes(b"")

    calls: list[int] = []

    def _alass_missing(*args, cancel_event=None, log_cb=None, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise AlassNotFoundError("alass binary not found: 'alass'")
        return True

    worker = _make_worker([(v1, s1), (v2, s2)], retimer=_alass_missing)
    cap = _capture(worker)
    worker.run()

    assert len(cap["finished"]) == 2
    idx, out_path, err = cap["finished"][0]
    assert (idx, out_path) == (0, None)
    assert "alass" in err.lower()
    # Pair 1 still ran and succeeded.
    assert cap["finished"][1][2] is None
    assert cap["queue_finished"] == [True]
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


# ---------------------------------------------------------------------------
# file_skipped signal exists on the worker
# ---------------------------------------------------------------------------


def test_file_skipped_signal_exists(qapp, tmp_path):
    """SubtitleRetimeWorker exposes a file_skipped(int, object) signal."""
    worker = _make_worker([], retimer=_fake_retimer_success)
    assert hasattr(worker, "file_skipped")


# ---------------------------------------------------------------------------
# Windows duplicate-subtitle bug: out_sub resolved against existing on-disk
# files so an overwrite replaces a visually-identical (NFC/NFD) twin in place.
# These reproduce on Linux because NFC and NFD are distinct dirents there too.
# ---------------------------------------------------------------------------


def _make_writing_retimer(captured: list[Path]):
    """Retimer stub that records and writes to the out_sub it is handed."""

    def _retimer(config, video, in_sub, out_sub, *, cancel_event=None, log_cb=None, **kwargs):
        captured.append(Path(out_sub))
        Path(out_sub).write_text("RETIMED")
        return True

    return _retimer


def test_overwrite_replaces_nfd_twin_in_place(qapp, tmp_path):
    """overwrite=True with an existing NFD-named output and an NFC-named input:
    the retimer must receive the EXISTING NFD path (not a fresh NFC one), so
    exactly one stem-matching .srt remains instead of a duplicate (fails pre-fix)."""
    video = tmp_path / "ep01.mkv"
    in_sub = tmp_path / (_NFC + ".srt")
    existing = tmp_path / (_NFD + "_retimed.srt")  # the pre-existing output on disk
    video.write_bytes(b"")
    in_sub.write_text("offtimed")
    existing.write_text("previous retime")

    captured: list[Path] = []
    worker = _make_worker([(video, in_sub)], overwrite=True, retimer=_make_writing_retimer(captured))
    worker.run()

    # Retimer targeted the existing NFD file byte-for-byte, not a new NFC name.
    assert captured == [existing]
    assert captured[0].name.encode("utf-8") == (_NFD + "_retimed.srt").encode("utf-8")
    # Exactly one retimed subtitle with this stem (no NFC/NFD twin).
    assert _nfc_stem_srt_count(tmp_path, _NFC + "_retimed") == 1


def test_overwrite_off_skips_nfd_twin(qapp, tmp_path):
    """overwrite=False with an existing NFD-named output and NFC-named input:
    must skip (resolver makes .exists() see the file) and NOT spawn a duplicate.
    Pre-fix this path silently created a twin even with overwrite unchecked."""
    video = tmp_path / "ep01.mkv"
    in_sub = tmp_path / (_NFC + ".srt")
    existing = tmp_path / (_NFD + "_retimed.srt")
    video.write_bytes(b"")
    in_sub.write_text("offtimed")
    existing.write_text("previous retime")

    calls: list[Path] = []
    worker = _make_worker([(video, in_sub)], overwrite=False, retimer=_make_writing_retimer(calls))
    cap = _capture(worker)
    worker.run()

    assert calls == []  # retimer not called
    assert len(cap["skipped"]) == 1
    assert cap["skipped"][0][1] == existing
    assert _nfc_stem_srt_count(tmp_path, _NFC + "_retimed") == 1


def test_source_sub_is_never_the_target(qapp, tmp_path):
    """The source subtitle keeps its own name and content: the output is a
    _retimed sibling even when the sub is the only subtitle in the folder and
    shares the video's stem. Mode-agnostic (single-file and folder-same-folder)."""
    video = tmp_path / (_NFC + ".mkv")
    in_sub = tmp_path / (_NFD + ".srt")  # source sub == the only on-disk subtitle
    video.write_bytes(b"")
    in_sub.write_text("orig")

    captured: list[Path] = []
    worker = _make_worker([(video, in_sub)], overwrite=True, retimer=_make_writing_retimer(captured))
    worker.run()

    assert captured == [tmp_path / (_NFD + "_retimed.srt")]
    assert in_sub.read_text() == "orig"
    assert _nfc_stem_srt_count(tmp_path, _NFC) == 1
    assert _nfc_stem_srt_count(tmp_path, _NFC + "_retimed") == 1

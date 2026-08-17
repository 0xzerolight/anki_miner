"""Tests for the ffsubsync sync engine (library call mocked)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.sync_engines.ffsubsync_engine import sync_with_ffsubsync

_RUN = "ffsubsync.ffsubsync.run"
_RESOLVE_FFMPEG = "anki_miner.services.sync_engines.ffsubsync_engine.resolve_ffmpeg"


@pytest.fixture()
def cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.ffmpeg_location = None
    return cfg


@pytest.fixture()
def paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    reference = tmp_path / "ref.srt"
    reference.touch()
    in_sub = tmp_path / "in.srt"
    in_sub.touch()
    out = tmp_path / "out.srt"
    return reference, in_sub, out


def _success_run(out_path: Path, offset: float = 1.5, scale: float = 1.0):
    def _run(args: Any, progress_handler: Any = None) -> dict[str, Any]:
        Path(out_path).touch()
        return {
            "retval": 0,
            "sync_was_successful": True,
            "offset_seconds": offset,
            "framerate_scale_factor": scale,
        }

    return _run


class TestSyncWithFfsubsync:
    def test_success_reports_offset_and_scale(self, cfg, paths):
        reference, in_sub, out = paths
        with patch(_RESOLVE_FFMPEG, return_value="ffmpeg"), patch(_RUN, side_effect=_success_run(out)):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)
        assert result.ok
        assert result.engine == "ffsubsync"
        assert result.offset_seconds == 1.5
        assert result.framerate_scale == 1.0

    def test_args_wire_reference_input_output_and_quality_gate(self, cfg, paths):
        reference, in_sub, out = paths
        captured: list[Any] = []

        def _run(args: Any, progress_handler: Any = None) -> dict[str, Any]:
            captured.append(args)
            Path(out).touch()
            return {"retval": 0, "sync_was_successful": True, "offset_seconds": 0.0, "framerate_scale_factor": 1.0}

        with patch(_RESOLVE_FFMPEG, return_value="/custom/ffmpeg"), patch(_RUN, side_effect=_run):
            sync_with_ffsubsync(cfg, reference, in_sub, out, split_penalty=8.0)

        args = captured[0]
        assert args.reference == str(reference)
        assert args.srtin == [str(in_sub)]
        assert args.srtout == str(out)
        assert args.ffmpeg_path == "/custom/ffmpeg"
        assert args.skip_sync_on_low_quality is True
        assert args.split_penalty == 8.0

    def test_split_mode_off_omits_split_penalty(self, cfg, paths):
        reference, in_sub, out = paths
        captured: list[Any] = []

        def _run(args: Any, progress_handler: Any = None) -> dict[str, Any]:
            captured.append(args)
            Path(out).touch()
            return {"retval": 0, "sync_was_successful": True, "offset_seconds": 0.0, "framerate_scale_factor": 1.0}

        with patch(_RESOLVE_FFMPEG, return_value="ffmpeg"), patch(_RUN, side_effect=_run):
            sync_with_ffsubsync(cfg, reference, in_sub, out, split_mode=False)

        assert captured[0].split_penalty is None

    def test_low_quality_rejection_unlinks_output(self, cfg, paths):
        """On a rejected sync ffsubsync writes the ORIGINAL to out — remove it."""
        reference, in_sub, out = paths

        def _run(args: Any, progress_handler: Any = None) -> dict[str, Any]:
            Path(out).touch()  # ffsubsync writes the unsynced original
            return {"retval": 0, "sync_was_successful": False, "offset_seconds": None, "framerate_scale_factor": None}

        with patch(_RESOLVE_FFMPEG, return_value="ffmpeg"), patch(_RUN, side_effect=_run):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)

        assert not result.ok
        assert "low-quality" in result.detail
        assert not out.exists()

    def test_engine_exception_is_a_failed_candidate(self, cfg, paths):
        reference, in_sub, out = paths
        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN, side_effect=RuntimeError("boom")),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)
        assert not result.ok
        assert "RuntimeError" in result.detail

    def test_pre_set_cancel_short_circuits(self, cfg, paths):
        import threading

        reference, in_sub, out = paths
        cancel = threading.Event()
        cancel.set()
        with patch(_RESOLVE_FFMPEG, return_value="ffmpeg"), patch(_RUN) as mock_run:
            result = sync_with_ffsubsync(cfg, reference, in_sub, out, cancel_event=cancel)
        assert not result.ok
        mock_run.assert_not_called()

    def test_log_cb_reports_offset(self, cfg, paths):
        reference, in_sub, out = paths
        received: list[str] = []
        with patch(_RESOLVE_FFMPEG, return_value="ffmpeg"), patch(_RUN, side_effect=_success_run(out, offset=-2.25)):
            sync_with_ffsubsync(cfg, reference, in_sub, out, log_cb=received.append)
        assert any("-2.25" in line for line in received)

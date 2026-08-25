"""Tests for the ffsubsync sync engine: supervised parent, child runner, dispatch.

The engine runs ffsubsync in a supervised child process, so the three halves are
tested separately: the parent (``run_supervised`` mocked), the child runner
(the ffsubsync library mocked), and the ``--ffsubsync-child`` dispatch (a real
subprocess against a stub ffsubsync package on ``PYTHONPATH``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.sync_engines import _ffsubsync_child
from anki_miner.services.sync_engines.ffsubsync_engine import (
    _FFSUBSYNC_TIMEOUT_S,
    sync_with_ffsubsync,
)
from anki_miner.utils.process_supervisor import SupervisedResult, SupervisedState

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_RUN_SUPERVISED = "anki_miner.services.sync_engines.ffsubsync_engine.run_supervised"
_RESOLVE_FFMPEG = "anki_miner.services.sync_engines.ffsubsync_engine.resolve_ffmpeg"
_LIB_RUN = "ffsubsync.ffsubsync.run"
_LIB_MAKE_PARSER = "ffsubsync.ffsubsync.make_parser"


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


def _verdict_line(
    *,
    successful: bool | None = True,
    retval: int | None = 0,
    offset: Any = 1.5,
    scale: Any = 1.0,
) -> str:
    return (
        json.dumps(
            {
                "retval": retval,
                "sync_was_successful": successful,
                "offset_seconds": offset,
                "framerate_scale_factor": scale,
            }
        )
        + "\n"
    )


def _supervised(
    stdout: str = "",
    *,
    state: SupervisedState = SupervisedState.COMPLETED,
    returncode: int | None = 0,
    stderr: str = "",
) -> SupervisedResult:
    return SupervisedResult(state, returncode, stdout, stderr)


def _completed(out: Path, **verdict: Any) -> Callable[..., SupervisedResult]:
    """A child that writes *out* and reports a verdict, like a real success."""

    def _run(command: Any, **kwargs: Any) -> SupervisedResult:
        Path(out).touch()
        return _supervised(_verdict_line(**verdict))

    return _run


def _child_argv(command: list[str]) -> list[str]:
    """The ffsubsync argv the child will parse, minus the re-entry prefix."""
    return list(command[command.index(_ffsubsync_child.CHILD_FLAG) + 1 :])


class TestSupervisedParent:
    def test_success_reports_offset_and_scale(self, cfg, paths):
        reference, in_sub, out = paths
        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_completed(out)),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)
        assert result.ok
        assert result.engine == "ffsubsync"
        assert result.offset_seconds == 1.5
        assert result.framerate_scale == 1.0

    def test_child_argv_matches_the_ffsubsync_cli_contract(self, cfg, paths):
        """The child parses this argv with ffsubsync's own parser — pin it there."""
        from ffsubsync.ffsubsync import make_parser

        reference, in_sub, out = paths
        captured: list[list[str]] = []

        def _run(command: Any, **kwargs: Any) -> SupervisedResult:
            captured.append([str(part) for part in command])
            Path(out).touch()
            return _supervised(_verdict_line())

        with (
            patch(_RESOLVE_FFMPEG, return_value="/custom/ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_run),
        ):
            sync_with_ffsubsync(cfg, reference, in_sub, out, split_penalty=8.0)

        args = make_parser().parse_args(_child_argv(captured[0]))
        assert args.reference == str(reference)
        assert args.srtin == [str(in_sub)]
        assert args.srtout == str(out)
        assert args.ffmpeg_path == "/custom/ffmpeg"
        assert args.skip_sync_on_low_quality is True
        assert args.split_penalty == 8.0

    def test_split_mode_off_omits_split_penalty(self, cfg, paths):
        from ffsubsync.ffsubsync import make_parser

        reference, in_sub, out = paths
        captured: list[list[str]] = []

        def _run(command: Any, **kwargs: Any) -> SupervisedResult:
            captured.append([str(part) for part in command])
            Path(out).touch()
            return _supervised(_verdict_line())

        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_run),
        ):
            sync_with_ffsubsync(cfg, reference, in_sub, out, split_mode=False)

        assert make_parser().parse_args(_child_argv(captured[0])).split_penalty is None

    def test_split_mode_off_reports_single_offset_engine_label(self, cfg, paths):
        """Matches alass_engine's `no_split` convention: label carries the mode, not just the tool."""
        reference, in_sub, out = paths
        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_completed(out)),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out, split_mode=False)
        assert result.engine == "ffsubsync (single offset)"

    def test_split_mode_on_reports_plain_engine_label(self, cfg, paths):
        reference, in_sub, out = paths
        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_completed(out)),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out, split_mode=True)
        assert result.engine == "ffsubsync"

    def test_low_quality_rejection_unlinks_output(self, cfg, paths):
        """On a rejected sync ffsubsync writes the ORIGINAL to out — remove it."""
        reference, in_sub, out = paths
        run = _completed(out, successful=False, offset=None, scale=None)

        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=run),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)

        assert not result.ok
        assert "low-quality" in result.detail
        assert not out.exists()

    def test_nonzero_retval_reports_an_engine_error(self, cfg, paths):
        """The verdict, not the exit status, distinguishes a reject from an error."""
        reference, in_sub, out = paths
        run = _completed(out, successful=False, retval=1, offset=None, scale=None)

        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=run),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)

        assert not result.ok
        assert result.detail == "ffsubsync error"
        assert not out.exists()

    def test_missing_output_file_is_a_failed_candidate(self, cfg, paths):
        reference, in_sub, out = paths
        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, return_value=_supervised(_verdict_line())),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)
        assert not result.ok

    def test_child_crash_without_a_verdict_is_a_failed_candidate(self, cfg, paths):
        reference, in_sub, out = paths
        out.touch()  # stale temp from a prior attempt; must not be left behind
        crashed = _supervised(state=SupervisedState.FAILED, returncode=1, stderr="Traceback ...\nRuntimeError: boom")

        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, return_value=crashed),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)

        assert not result.ok
        assert result.engine == "ffsubsync"
        assert "exit 1" in result.detail
        assert not out.exists()

    def test_completed_child_with_unparseable_stdout_is_a_failed_candidate(self, cfg, paths):
        reference, in_sub, out = paths
        out.touch()
        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, return_value=_supervised("not json at all\n")),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)
        assert not result.ok
        assert not out.exists()

    def test_verdict_survives_stray_stdout_noise(self, cfg, paths):
        reference, in_sub, out = paths

        def _run(command: Any, **kwargs: Any) -> SupervisedResult:
            Path(out).touch()
            return _supervised("some library chatter\n[]\n" + _verdict_line(offset=-0.75))

        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_run),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)

        assert result.ok
        assert result.offset_seconds == -0.75

    def test_non_numeric_offsets_do_not_reach_the_result(self, cfg, paths):
        """SyncResult's float fields are typed; the verdict arrives as untyped JSON."""
        reference, in_sub, out = paths
        received: list[str] = []

        def _run(command: Any, **kwargs: Any) -> SupervisedResult:
            Path(out).touch()
            return _supervised(_verdict_line(offset="soon", scale="fast"))

        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_run),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out, log_cb=received.append)

        assert result.ok
        assert result.offset_seconds is None
        assert result.framerate_scale is None
        assert received == []

    @pytest.mark.parametrize(
        "state",
        [SupervisedState.TIMED_OUT, SupervisedState.CANCELLED],
    )
    def test_timeout_and_cancel_map_to_failure_and_unlink(self, cfg, paths, state):
        reference, in_sub, out = paths
        out.touch()
        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, return_value=_supervised(state=state, returncode=None)),
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out)
        assert not result.ok
        assert result.detail == state.value
        assert not out.exists()

    def test_run_is_bounded_and_cancellable(self, cfg, paths):
        reference, in_sub, out = paths
        cancel = threading.Event()
        captured: dict[str, Any] = {}

        def _run(command: Any, **kwargs: Any) -> SupervisedResult:
            captured.update(kwargs)
            Path(out).touch()
            return _supervised(_verdict_line())

        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_run),
        ):
            sync_with_ffsubsync(cfg, reference, in_sub, out, cancel_event=cancel)

        assert captured["timeout_s"] == _FFSUBSYNC_TIMEOUT_S == 60 * 60
        assert captured["cancel"] is cancel

    def test_frozen_build_reenters_the_app_binary(self, cfg, paths, monkeypatch):
        """A frozen bundle ships no interpreter: the child IS the app, re-entered."""
        reference, in_sub, out = paths
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", "/opt/anki_miner/anki_miner_gui")
        captured: list[list[str]] = []

        def _run(command: Any, **kwargs: Any) -> SupervisedResult:
            captured.append([str(part) for part in command])
            Path(out).touch()
            return _supervised(_verdict_line())

        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_run),
        ):
            sync_with_ffsubsync(cfg, reference, in_sub, out)

        assert captured[0][:2] == ["/opt/anki_miner/anki_miner_gui", _ffsubsync_child.CHILD_FLAG]

    def test_dev_build_reenters_through_the_module_entry_point(self, cfg, paths, monkeypatch):
        reference, in_sub, out = paths
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "executable", "/venv/bin/python")
        captured: list[list[str]] = []

        def _run(command: Any, **kwargs: Any) -> SupervisedResult:
            captured.append([str(part) for part in command])
            Path(out).touch()
            return _supervised(_verdict_line())

        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_run),
        ):
            sync_with_ffsubsync(cfg, reference, in_sub, out)

        assert captured[0][:4] == [
            "/venv/bin/python",
            "-m",
            "anki_miner",
            _ffsubsync_child.CHILD_FLAG,
        ]

    def test_pre_set_cancel_short_circuits(self, cfg, paths):
        reference, in_sub, out = paths
        cancel = threading.Event()
        cancel.set()
        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED) as mock_run,
        ):
            result = sync_with_ffsubsync(cfg, reference, in_sub, out, cancel_event=cancel)
        assert not result.ok
        mock_run.assert_not_called()

    def test_log_cb_reports_offset(self, cfg, paths):
        reference, in_sub, out = paths
        received: list[str] = []
        with (
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RUN_SUPERVISED, side_effect=_completed(out, offset=-2.25)),
        ):
            sync_with_ffsubsync(cfg, reference, in_sub, out, log_cb=received.append)
        assert any("-2.25" in line for line in received)


def _engine_argv(tmp_path: Path) -> list[str]:
    """An argv of the shape the engine builds, for the child to parse."""
    return [
        str(tmp_path / "ref.srt"),
        "-i",
        str(tmp_path / "in.srt"),
        "-o",
        str(tmp_path / "out.srt"),
        "--ffmpeg-path",
        "ffmpeg",
        "--skip-sync-on-low-quality",
        "--quality-max-offset-seconds",
        "120.0",
        "--split-penalty",
        "8.0",
    ]


class TestChildRunner:
    def test_prints_exactly_one_verdict_line(self, tmp_path, capfd):
        def _run(args: Any, progress_handler: Any = None) -> dict[str, Any]:
            return {
                "retval": 0,
                "sync_was_successful": True,
                "offset_seconds": 1.5,
                "framerate_scale_factor": 1.001,
            }

        with patch(_LIB_RUN, side_effect=_run):
            assert _ffsubsync_child.main(_engine_argv(tmp_path)) == 0

        lines = capfd.readouterr().out.splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {
            "retval": 0,
            "sync_was_successful": True,
            "offset_seconds": 1.5,
            "framerate_scale_factor": 1.001,
        }

    def test_library_stdout_noise_stays_off_the_verdict_stream(self, tmp_path, capfd):
        def _run(args: Any, progress_handler: Any = None) -> dict[str, Any]:
            print("50")  # ffsubsync's vlc-mode progress goes to stdout
            return {"retval": 0, "sync_was_successful": True, "offset_seconds": 0.0, "framerate_scale_factor": 1.0}

        with patch(_LIB_RUN, side_effect=_run):
            assert _ffsubsync_child.main(_engine_argv(tmp_path)) == 0

        captured = capfd.readouterr()
        assert len(captured.out.splitlines()) == 1
        assert "50" in captured.err

    def test_non_native_numbers_are_coerced_for_json(self, tmp_path, capfd):
        class _NumpyLike:
            def __float__(self) -> float:
                return -0.5

        def _run(args: Any, progress_handler: Any = None) -> dict[str, Any]:
            return {
                "retval": 0,
                "sync_was_successful": 1,
                "offset_seconds": _NumpyLike(),
                "framerate_scale_factor": _NumpyLike(),
            }

        with patch(_LIB_RUN, side_effect=_run):
            assert _ffsubsync_child.main(_engine_argv(tmp_path)) == 0

        verdict = json.loads(capfd.readouterr().out)
        assert verdict["sync_was_successful"] is True
        assert verdict["offset_seconds"] == -0.5

    def test_absent_verdict_keys_come_back_null(self, tmp_path, capfd):
        """ffsubsync's early-validation return carries no sync_was_successful key."""

        def _run(args: Any, progress_handler: Any = None) -> dict[str, Any]:
            return {"retval": 1, "offset_seconds": None, "framerate_scale_factor": None}

        with patch(_LIB_RUN, side_effect=_run):
            assert _ffsubsync_child.main(_engine_argv(tmp_path)) == 0

        assert json.loads(capfd.readouterr().out)["sync_was_successful"] is None

    def test_uncoercible_value_costs_its_key_not_the_verdict(self, tmp_path, capfd):
        """A sync that ran writes its output — a verdict-less exit 1 would unlink it."""

        class _Hostile:
            def __float__(self) -> float:
                raise ValueError("not a number")

        def _run(args: Any, progress_handler: Any = None) -> dict[str, Any]:
            return {
                "retval": 0,
                "sync_was_successful": True,
                "offset_seconds": _Hostile(),
                "framerate_scale_factor": 1.0,
            }

        with patch(_LIB_RUN, side_effect=_run):
            assert _ffsubsync_child.main(_engine_argv(tmp_path)) == 0

        verdict = json.loads(capfd.readouterr().out)
        assert verdict["offset_seconds"] is None
        assert verdict["sync_was_successful"] is True
        assert verdict["framerate_scale_factor"] == 1.0

    def test_library_exception_exits_nonzero_without_a_verdict(self, tmp_path, capfd):
        with patch(_LIB_RUN, side_effect=RuntimeError("boom")):
            assert _ffsubsync_child.main(_engine_argv(tmp_path)) == 1
        assert capfd.readouterr().out == ""

    def test_rejected_argv_systemexit_exits_nonzero(self, tmp_path, capfd):
        """argparse's parser.error() raises SystemExit (a BaseException) — must not escape."""
        parser = MagicMock()
        parser.parse_args.side_effect = SystemExit(2)
        with patch(_LIB_MAKE_PARSER, return_value=parser):
            assert _ffsubsync_child.main(_engine_argv(tmp_path)) == 1
        assert capfd.readouterr().out == ""


_STUB_FFSUBSYNC = '''\
"""Stand-in for the ffsubsync package: no ffmpeg, no numpy, no network."""

import argparse
import sys


def make_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("reference")
    parser.add_argument("-i", "--srtin", action="append")
    parser.add_argument("-o", "--srtout")
    parser.add_argument("--ffmpeg-path")
    parser.add_argument("--skip-sync-on-low-quality", action="store_true")
    parser.add_argument("--quality-max-offset-seconds", type=float)
    parser.add_argument("--split-penalty", type=float)
    parser.add_argument("--reference-stream")
    return parser


def run(args, progress_handler=None):
    print("library noise on stdout")
    print(
        "APP_IMPORTED=%s QTWIDGETS_IMPORTED=%s"
        % (
            "anki_miner.gui.app" in sys.modules,
            "PyQt6.QtWidgets" in sys.modules,
        ),
        file=sys.stderr,
    )
    with open(args.srtout, "w", encoding="utf-8") as handle:
        handle.write("")
    return {
        "retval": 0,
        "sync_was_successful": True,
        "offset_seconds": 2.5,
        "framerate_scale_factor": 1.0,
    }
'''


class TestChildDispatch:
    def test_launch_flag_matches_the_child_constant(self):
        """launch.py may not import the child module at boot — the two literals are pinned instead."""
        from anki_miner.gui import launch

        assert launch.FFSUBSYNC_CHILD_FLAG == _ffsubsync_child.CHILD_FLAG

    def test_module_entry_runs_the_child_without_booting_the_app(self, tmp_path):
        stub_root = tmp_path / "stubs"
        (stub_root / "ffsubsync").mkdir(parents=True)
        (stub_root / "ffsubsync" / "__init__.py").write_text("", encoding="utf-8")
        (stub_root / "ffsubsync" / "ffsubsync.py").write_text(_STUB_FFSUBSYNC, encoding="utf-8")

        (tmp_path / "ref.srt").touch()
        (tmp_path / "in.srt").touch()

        env = os.environ.copy()
        env["ANKI_MINER_HOME"] = str(tmp_path / "home")
        # PYTHONPATH precedes site-packages, so the stub shadows the real package.
        env["PYTHONPATH"] = os.pathsep.join((str(stub_root), str(PROJECT_ROOT), env.get("PYTHONPATH", "")))

        result = subprocess.run(
            [sys.executable, "-m", "anki_miner", _ffsubsync_child.CHILD_FLAG, *_engine_argv(tmp_path)],
            cwd=PROJECT_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert len(lines) == 1, result.stdout
        assert json.loads(lines[0]) == {
            "retval": 0,
            "sync_was_successful": True,
            "offset_seconds": 2.5,
            "framerate_scale_factor": 1.0,
        }
        assert "library noise on stdout" in result.stderr
        # The dispatch runs before the crash sink, the app mutex and any app
        # import: no GUI, no instance-lock contention with the parent.
        assert "APP_IMPORTED=False QTWIDGETS_IMPORTED=False" in result.stderr
        assert not (tmp_path / "home").exists()

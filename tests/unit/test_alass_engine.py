"""Tests for the alass sync engine (subprocess interaction mocked)."""

from __future__ import annotations

import io
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.services.sync_engines.alass_engine import (
    _parse_block_shifts,
    _parse_warnings,
    sync_with_alass,
)
from anki_miner.utils.process_supervisor import SupervisedResult, SupervisedState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakePopen:
    """Minimal subprocess.Popen stand-in consumed by run_supervised."""

    def __init__(self, lines: list[str], returncode: int = 0, *, pid: int = 12345) -> None:
        self.pid = pid
        self.returncode: int | None = returncode
        self._final_returncode = returncode
        output = "".join(line if line.endswith("\n") else f"{line}\n" for line in lines)
        self.stdout = io.BytesIO(output.encode("utf-8"))
        self.stderr = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._final_returncode
        return self._final_returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def __enter__(self) -> _FakePopen:
        return self

    def __exit__(self, *_: Any) -> None:
        pass


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.alass_location = None
    cfg.ffmpeg_location = None
    cfg.ffprobe_location = None
    return cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def video(tmp_path: Path) -> Path:
    p = tmp_path / "ep01.mkv"
    p.touch()
    return p


@pytest.fixture()
def in_sub(tmp_path: Path) -> Path:
    p = tmp_path / "ep01.srt"
    p.touch()
    return p


@pytest.fixture()
def out_sub(tmp_path: Path) -> Path:
    return tmp_path / "ep01.retime-cand.srt"


@pytest.fixture()
def cfg() -> MagicMock:
    return _make_config()


@pytest.fixture(autouse=True)
def stub_supervisor_killpg():
    with patch("anki_miner.utils.process_supervisor.os.killpg"):
        yield


_POPEN = "anki_miner.utils.process_supervisor.subprocess.Popen"
_RESOLVE_ALASS = "anki_miner.services.sync_engines.alass_engine.resolve_alass"
_RESOLVE_FFMPEG = "anki_miner.services.sync_engines.alass_engine.resolve_ffmpeg"
_RESOLVE_FFPROBE = "anki_miner.services.sync_engines.alass_engine.resolve_ffprobe"
_OS_KILLPG = "anki_miner.utils.process_supervisor.os.killpg"


def _run(cfg, video, in_sub, out_sub, factory, **kwargs):
    with (
        patch(_RESOLVE_ALASS, return_value="alass"),
        patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
        patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
        patch(_POPEN, side_effect=factory),
    ):
        return sync_with_alass(cfg, video, in_sub, out_sub, sub_reference=kwargs.pop("sub_reference", False), **kwargs)


def _touch_factory(captured: list[list[str]], lines: list[str] | None = None, returncode: int = 0):
    def _factory(cmd: list[str], **_: Any) -> _FakePopen:
        captured.append(cmd)
        if returncode == 0:
            Path(cmd[-1]).touch()
        return _FakePopen(lines or [], returncode=returncode)

    return _factory


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


class TestParsing:
    #: Verbatim tail of a real alass v2.0.0 run.
    _REAL_OUTPUT = (
        "info: 'reference file FPS/input file FPS' ratio is 25/24\n"
        "shifted block of 3 subtitles with length 0:00:06.000 by -0:00:04.916\n"
        "shifted block of 4 subtitles with length 0:00:09.000 by -0:00:05.722\n"
        "shifted block of 3 subtitles with length 0:01:06.000 by 0:00:15.167\n"
    )

    def test_parses_real_block_shift_lines(self):
        shifts = _parse_block_shifts(self._REAL_OUTPUT)
        assert shifts == pytest.approx((-4.916, -5.722, 15.167))

    def test_no_blocks_parses_empty(self):
        assert _parse_block_shifts("nothing here") == ()

    def test_fps_ratio_guess_is_a_warning(self):
        warnings = _parse_warnings(self._REAL_OUTPUT)
        assert any("25/24" in w for w in warnings)

    def test_unity_fps_ratio_is_not_a_warning(self):
        assert _parse_warnings("info: 'reference file FPS/input file FPS' ratio is 24/24\n") == ()

    def test_negative_timestamp_warning_captured(self):
        out = "warn: some subtitles now have negative timings, which can cause problems\n"
        warnings = _parse_warnings(out)
        assert len(warnings) == 1
        assert "negative" in warnings[0]


# ---------------------------------------------------------------------------
# Argument construction
# ---------------------------------------------------------------------------


class TestArgConstruction:
    def test_flag_order_and_positionals(self, video, in_sub, out_sub, cfg):
        captured: list[list[str]] = []
        result = _run(cfg, video, in_sub, out_sub, _touch_factory(captured), split_penalty=15.0)

        assert result.ok
        cmd = captured[0]
        assert cmd[0] == "alass"
        sp_idx = cmd.index("--split-penalty")
        assert sp_idx < len(cmd) - 3
        assert cmd[sp_idx + 1] == "15.0"
        assert cmd[-3] == str(video)
        assert cmd[-2] == str(in_sub)
        assert cmd[-1] == str(out_sub)

    def test_default_split_penalty_7(self, video, in_sub, out_sub, cfg):
        captured: list[list[str]] = []
        _run(cfg, video, in_sub, out_sub, _touch_factory(captured))
        cmd = captured[0]
        assert cmd[cmd.index("--split-penalty") + 1] == "7.0"

    def test_fps_guessing_always_disabled(self, video, in_sub, out_sub, cfg):
        """alass's FPS guessing misfires on same-framerate pairs; never enabled."""
        captured: list[list[str]] = []
        _run(cfg, video, in_sub, out_sub, _touch_factory(captured))
        assert "--disable-fps-guessing" in captured[0]
        assert "--no-split" not in captured[0]

    def test_no_split_flag_present_when_requested(self, video, in_sub, out_sub, cfg):
        captured: list[list[str]] = []
        result = _run(cfg, video, in_sub, out_sub, _touch_factory(captured), no_split=True)
        assert "--no-split" in captured[0]
        assert result.engine == "alass (single offset)"

    def test_sub_reference_flags(self, video, in_sub, out_sub, cfg):
        in_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n\n", encoding="utf-8")
        captured: list[list[str]] = []
        _run(cfg, video, in_sub, out_sub, _touch_factory(captured), sub_reference=True)
        cmd = captured[0]
        assert cmd[cmd.index("--speed-optimization") + 1] == "0"
        assert cmd[cmd.index("--encoding-ref") + 1] == "utf-8"
        assert cmd[cmd.index("--encoding-inc") + 1] == "utf-8"

    def test_audio_path_keeps_alass_speed_default(self, video, in_sub, out_sub, cfg):
        captured: list[list[str]] = []
        _run(cfg, video, in_sub, out_sub, _touch_factory(captured), sub_reference=False)
        assert "--speed-optimization" not in captured[0]
        assert "--encoding-ref" not in captured[0]

    def test_cp932_input_declared_as_shift_jis(self, video, in_sub, out_sub, cfg):
        """alass panics on ``cp932``; the WHATWG label must be used instead."""
        in_sub.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n\n".encode("cp932"))
        captured: list[list[str]] = []
        _run(cfg, video, in_sub, out_sub, _touch_factory(captured))
        assert captured[0][captured[0].index("--encoding-inc") + 1] == "shift_jis"

    def test_euc_jp_input_declared_as_euc_jp(self, video, in_sub, out_sub, cfg):
        in_sub.write_bytes("1\n00:00:01,000 --> 00:00:02,000\n猫が走る\n\n".encode("euc_jp"))
        captured: list[list[str]] = []
        _run(cfg, video, in_sub, out_sub, _touch_factory(captured))
        assert captured[0][captured[0].index("--encoding-inc") + 1] == "euc-jp"


class TestEnvInjection:
    def test_env_contains_alass_ff_paths_and_parent_env(self, video, in_sub, out_sub, cfg):
        captured_env: list[dict[str, str]] = []

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured_env.append(kwargs.get("env", {}))
            Path(cmd[-1]).touch()
            return _FakePopen([], returncode=0)

        sentinel_key = "_ANKI_MINER_TEST_SENTINEL_12345"
        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="/custom/ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="/custom/ffprobe"),
            patch(_POPEN, side_effect=_factory),
            patch.dict(os.environ, {sentinel_key: "sentinel"}),
        ):
            sync_with_alass(cfg, video, in_sub, out_sub, sub_reference=False)

        env = captured_env[0]
        assert env["ALASS_FFMPEG_PATH"] == "/custom/ffmpeg"
        assert env["ALASS_FFPROBE_PATH"] == "/custom/ffprobe"
        assert env.get(sentinel_key) == "sentinel"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class TestResults:
    def test_success_carries_block_shifts_and_warnings(self, video, in_sub, out_sub, cfg):
        lines = [
            "shifted block of 3 subtitles with length 0:00:06.000 by -0:00:04.916",
            "warn: some subtitles now have negative timings",
        ]
        captured: list[list[str]] = []
        result = _run(cfg, video, in_sub, out_sub, _touch_factory(captured, lines))
        assert result.ok
        assert result.block_shifts_seconds == (-4.916,)
        assert any("negative" in w for w in result.warnings)

    def test_exit_nonzero_returns_failure(self, video, in_sub, out_sub, cfg, caplog):
        import logging

        captured: list[list[str]] = []
        with caplog.at_level(logging.WARNING, logger="anki_miner.services.sync_engines.alass_engine"):
            result = _run(
                cfg,
                video,
                in_sub,
                out_sub,
                _touch_factory(captured, ["error: could not parse reference file"], returncode=1),
            )
        assert not result.ok
        assert not out_sub.exists()
        assert any("error: could not parse reference file" in r.getMessage() for r in caplog.records)

    def test_partial_out_cleaned_on_failure(self, video, in_sub, out_sub, cfg):
        def _factory(cmd: list[str], **_: Any) -> _FakePopen:
            Path(cmd[-1]).touch()  # partial output before failing
            return _FakePopen([], returncode=1)

        result = _run(cfg, video, in_sub, out_sub, _factory)
        assert not result.ok
        assert not out_sub.exists()

    def test_log_cb_receives_stdout_lines(self, video, in_sub, out_sub, cfg):
        lines = ["info: analysing audio", "done"]
        received: list[str] = []
        captured: list[list[str]] = []
        _run(cfg, video, in_sub, out_sub, _touch_factory(captured, lines), log_cb=received.append)
        assert received == lines

    def test_file_not_found_raises_alass_not_found(self, video, in_sub, out_sub, cfg):
        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            raise FileNotFoundError("alass: No such file or directory")

        with pytest.raises(AlassNotFoundError):
            _run(cfg, video, in_sub, out_sub, _factory)

    def test_timeout_reported_with_timeout_arg(self, video, in_sub, out_sub, cfg):
        captured: dict[str, Any] = {}

        def timed_out(_command: list[str], **kwargs: Any) -> SupervisedResult:
            captured.update(kwargs)
            return SupervisedResult(SupervisedState.TIMED_OUT, -signal.SIGKILL, "", "")

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(
                "anki_miner.services.sync_engines.alass_engine.run_supervised",
                side_effect=timed_out,
            ),
        ):
            result = sync_with_alass(cfg, video, in_sub, out_sub, sub_reference=False)

        assert not result.ok
        assert captured["timeout_s"] == 60 * 60
        assert captured["combine_stderr"] is True


# ---------------------------------------------------------------------------
# Cancellation (POSIX kill path)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX kill path only")
class TestCancellationPosix:
    def test_presignalled_cancel_returns_failure_without_error_log(self, video, in_sub, out_sub, cfg, caplog):
        import logging

        cancel_event = threading.Event()
        cancel_event.set()

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            Path(cmd[-1]).touch()
            return _FakePopen(["info: analysing…"], returncode=0)

        with caplog.at_level(logging.WARNING, logger="anki_miner.services.sync_engines.alass_engine"):
            result = _run(cfg, video, in_sub, out_sub, _factory, cancel_event=cancel_event)

        assert not result.ok
        assert not out_sub.exists()
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_cancel_while_process_alive_kills(self, video, in_sub, out_sub, cfg):
        """Cancel arriving while the process is still streaming → os.killpg fires."""
        cancel_event = threading.Event()
        killed_event = threading.Event()
        fake: _FakePopen | None = None

        def _killpg(pgid: int, sig: int) -> None:
            assert fake is not None
            fake.returncode = -sig
            killed_event.set()

        class _StreamingPipe:
            def __init__(self) -> None:
                self._read = False

            def read(self, _size: int) -> bytes:
                if self._read:
                    return b""
                self._read = True
                cancel_event.set()
                killed_event.wait(timeout=5.0)
                return b"info: analysing audio\n"

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            nonlocal fake
            fake = _FakePopen([], returncode=0)
            fake.returncode = None
            fake.stdout = _StreamingPipe()  # type: ignore[assignment]
            return fake

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
            patch(_OS_KILLPG, side_effect=_killpg) as mock_killpg,
        ):
            result = sync_with_alass(cfg, video, in_sub, out_sub, sub_reference=False, cancel_event=cancel_event)

        assert not result.ok
        mock_killpg.assert_any_call(12345, signal.SIGTERM)
        mock_killpg.assert_any_call(12345, signal.SIGKILL)
        assert killed_event.is_set()

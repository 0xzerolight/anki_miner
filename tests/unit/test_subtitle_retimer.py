"""Tests for anki_miner.services.subtitle_retimer.

All subprocess interaction is mocked — no real alass binary is required.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.services.subtitle_retimer import retime_subtitle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakePopen:
    """Minimal subprocess.Popen stand-in consumed by retime_subtitle."""

    def __init__(
        self,
        lines: list[str],
        returncode: int = 0,
        *,
        pid: int = 12345,
        create_tmp: bool = True,
        tmp_path_ref: list[Path] | None = None,
    ) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._lines = lines
        self._final_returncode = returncode
        self._create_tmp = create_tmp
        self._tmp_path_ref = tmp_path_ref  # mutable list so caller can inspect

        # stdout is an iterable of the provided lines (already stripped of \n
        # by design; the implementation strips trailing newlines itself)
        self.stdout = iter(lines)

    def wait(self) -> int:
        self.returncode = self._final_returncode
        return self._final_returncode

    def kill(self) -> None:  # Windows path
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


def _popen_factory(
    lines: list[str],
    returncode: int = 0,
    *,
    pid: int = 12345,
    create_tmp: bool = True,
    tmp_path_ref: list[Path] | None = None,
) -> _FakePopen:
    """Return a factory closure suitable for patching subprocess.Popen."""

    fake = _FakePopen(
        lines,
        returncode,
        pid=pid,
        create_tmp=create_tmp,
        tmp_path_ref=tmp_path_ref,
    )

    def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
        # If the fake should create the tmp file, extract it from the command
        # (last positional argument = tmp_out path).
        if create_tmp and len(cmd) >= 1:
            tmp_path = Path(cmd[-1])
            tmp_path.touch()
            if tmp_path_ref is not None:
                tmp_path_ref.clear()
                tmp_path_ref.append(tmp_path)

        return fake

    return _factory  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def video(tmp_dir: Path) -> Path:
    p = tmp_dir / "ep01.mkv"
    p.touch()
    return p


@pytest.fixture()
def in_sub(tmp_dir: Path) -> Path:
    p = tmp_dir / "ep01.srt"
    p.touch()
    return p


@pytest.fixture()
def out_sub(tmp_dir: Path) -> Path:
    return tmp_dir / "ep01_retimed.srt"


@pytest.fixture()
def cfg() -> MagicMock:
    return _make_config()


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_POPEN = "anki_miner.services.subtitle_retimer.subprocess.Popen"
_RESOLVE_ALASS = "anki_miner.services.subtitle_retimer.resolve_alass"
_RESOLVE_FFMPEG = "anki_miner.services.subtitle_retimer.resolve_ffmpeg"
_RESOLVE_FFPROBE = "anki_miner.services.subtitle_retimer.resolve_ffprobe"
_OS_KILLPG = "anki_miner.services.subtitle_retimer.os.killpg"
_OS_GETPGID = "anki_miner.services.subtitle_retimer.os.getpgid"
_OS_REPLACE = "anki_miner.services.subtitle_retimer.os.replace"


# ---------------------------------------------------------------------------
# Test: Argument construction
# ---------------------------------------------------------------------------


class TestArgConstruction:
    def test_split_penalty_before_positionals(
        self, tmp_dir: Path, video: Path, in_sub: Path, out_sub: Path, cfg: MagicMock
    ) -> None:
        """--split-penalty flag must appear before the three positional paths."""
        captured_cmd: list[list[str]] = []

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured_cmd.append(cmd)
            tmp_path = Path(cmd[-1])
            tmp_path.touch()
            fake = _FakePopen([], returncode=0)
            return fake

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
        ):
            result = retime_subtitle(cfg, video, in_sub, out_sub, split_penalty=15.0)

        assert result is True
        cmd = captured_cmd[0]
        assert cmd[0] == "alass"
        # --split-penalty and its value come before the three positional paths
        sp_idx = cmd.index("--split-penalty")
        # The three positionals are the last three elements
        assert sp_idx < len(cmd) - 3
        assert cmd[sp_idx + 1] == "15.0"
        # Positional order: video, in_sub, tmp_out
        assert cmd[-3] == str(video)
        assert cmd[-2] == str(in_sub)
        # Last element is the tmp file (not equal to out_sub)
        assert cmd[-1] != str(out_sub)

    def test_default_split_penalty_7(self, video: Path, in_sub: Path, out_sub: Path, cfg: MagicMock) -> None:
        captured_cmd: list[list[str]] = []

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured_cmd.append(cmd)
            Path(cmd[-1]).touch()
            return _FakePopen([], returncode=0)

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
        ):
            retime_subtitle(cfg, video, in_sub, out_sub)

        cmd = captured_cmd[0]
        sp_idx = cmd.index("--split-penalty")
        assert cmd[sp_idx + 1] == "7"


# ---------------------------------------------------------------------------
# Test: Temp path
# ---------------------------------------------------------------------------


class TestTempPath:
    def test_tmp_in_out_sub_parent_with_correct_suffix(
        self, video: Path, in_sub: Path, out_sub: Path, cfg: MagicMock
    ) -> None:
        """Temp file must be in out_sub.parent and carry out_sub's suffix."""
        captured_tmp: list[Path] = []

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            tmp = Path(cmd[-1])
            captured_tmp.append(tmp)
            tmp.touch()
            return _FakePopen([], returncode=0)

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
        ):
            result = retime_subtitle(cfg, video, in_sub, out_sub)

        assert result is True
        tmp = captured_tmp[0]
        assert tmp.parent == out_sub.parent
        assert tmp.suffix == out_sub.suffix
        assert tmp != out_sub
        # The tmp file should NOT exist after success (it was replaced)
        assert not tmp.exists()
        assert out_sub.exists()

    def test_success_calls_os_replace(self, video: Path, in_sub: Path, out_sub: Path, cfg: MagicMock) -> None:
        """os.replace(tmp, out_sub) is called on success."""
        captured_tmp: list[Path] = []

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            tmp = Path(cmd[-1])
            captured_tmp.append(tmp)
            tmp.touch()
            return _FakePopen([], returncode=0)

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
            patch(_OS_REPLACE) as mock_replace,
        ):
            result = retime_subtitle(cfg, video, in_sub, out_sub)

        assert result is True
        assert mock_replace.call_count == 1
        args = mock_replace.call_args[0]
        assert args[0] == captured_tmp[0]
        assert args[1] == out_sub

    def test_ass_extension_preserved(self, tmp_path: Path, video: Path, in_sub: Path, cfg: MagicMock) -> None:
        """.ass out_sub → tmp suffix must be .ass, not .srt."""
        out_sub_ass = tmp_path / "ep01_retimed.ass"
        captured_tmp: list[Path] = []

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            tmp = Path(cmd[-1])
            captured_tmp.append(tmp)
            tmp.touch()
            return _FakePopen([], returncode=0)

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
        ):
            result = retime_subtitle(cfg, video, in_sub, out_sub_ass)

        assert result is True
        assert captured_tmp[0].suffix == ".ass"


# ---------------------------------------------------------------------------
# Test: in_sub == out_sub aliasing
# ---------------------------------------------------------------------------


class TestAliasing:
    def test_alias_safe(self, tmp_path: Path, video: Path, cfg: MagicMock) -> None:
        """When in_sub == out_sub, the tmp path must differ from both."""
        sub = tmp_path / "ep01.srt"
        sub.touch()

        captured_tmp: list[Path] = []

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            tmp = Path(cmd[-1])
            captured_tmp.append(tmp)
            tmp.touch()
            return _FakePopen([], returncode=0)

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
            patch(_OS_REPLACE) as mock_replace,
        ):
            result = retime_subtitle(cfg, video, sub, sub)

        assert result is True
        tmp = captured_tmp[0]
        assert tmp != sub
        # Replace still targets the original sub path
        assert mock_replace.call_args[0][1] == sub


# ---------------------------------------------------------------------------
# Test: Env injection
# ---------------------------------------------------------------------------


class TestEnvInjection:
    def test_env_contains_alass_ff_paths_and_parent_env(
        self, video: Path, in_sub: Path, out_sub: Path, cfg: MagicMock
    ) -> None:
        """ALASS_FFMPEG_PATH and ALASS_FFPROBE_PATH are set; parent env preserved."""
        captured_env: list[dict[str, str]] = []

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured_env.append(kwargs.get("env", {}))
            Path(cmd[-1]).touch()
            return _FakePopen([], returncode=0)

        sentinel_key = "_ANKI_MINER_TEST_SENTINEL_12345"
        sentinel_val = "test_sentinel_value"

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="/custom/ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="/custom/ffprobe"),
            patch(_POPEN, side_effect=_factory),
            patch.dict(os.environ, {sentinel_key: sentinel_val}),
        ):
            retime_subtitle(cfg, video, in_sub, out_sub)

        env = captured_env[0]
        assert env["ALASS_FFMPEG_PATH"] == "/custom/ffmpeg"
        assert env["ALASS_FFPROBE_PATH"] == "/custom/ffprobe"
        assert env.get(sentinel_key) == sentinel_val


# ---------------------------------------------------------------------------
# Test: stdout → log_cb
# ---------------------------------------------------------------------------


class TestLogCallback:
    def test_all_lines_reach_log_cb(self, video: Path, in_sub: Path, out_sub: Path, cfg: MagicMock) -> None:
        """Every line emitted to stdout by alass reaches log_cb (newline stripped)."""
        lines = ["info: analysing audio", "shifted block 1 by 300ms", "done"]
        # Feed lines as-is (no trailing \n) to simulate stripped iteration

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            Path(cmd[-1]).touch()
            return _FakePopen(lines, returncode=0)

        received: list[str] = []

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
        ):
            retime_subtitle(cfg, video, in_sub, out_sub, log_cb=received.append)

        assert received == lines

    def test_lines_with_trailing_newline_stripped(
        self, video: Path, in_sub: Path, out_sub: Path, cfg: MagicMock
    ) -> None:
        """Lines that arrive with trailing \\n are stripped before log_cb."""
        lines_with_nl = ["hello\n", "world\n"]

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            Path(cmd[-1]).touch()
            fake = _FakePopen.__new__(_FakePopen)
            fake.pid = 12345
            fake.returncode = None
            fake._final_returncode = 0
            fake.stdout = iter(lines_with_nl)

            def wait() -> int:
                fake.returncode = 0
                return 0

            fake.wait = wait  # type: ignore[method-assign]
            fake.kill = lambda: None  # type: ignore[method-assign]
            return fake

        received: list[str] = []

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
        ):
            retime_subtitle(cfg, video, in_sub, out_sub, log_cb=received.append)

        assert received == ["hello", "world"]


# ---------------------------------------------------------------------------
# Test: Exit nonzero / failure
# ---------------------------------------------------------------------------


class TestFailure:
    def test_exit_nonzero_returns_false(
        self, video: Path, in_sub: Path, out_sub: Path, cfg: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """returncode=1 and no tmp file → returns False, does not write out_sub."""
        lines = ["error: could not parse reference file"]

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            # Don't create tmp file — failure case
            return _FakePopen(lines, returncode=1, create_tmp=False)

        import logging

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
            caplog.at_level(logging.WARNING, logger="anki_miner.services.subtitle_retimer"),
        ):
            result = retime_subtitle(cfg, video, in_sub, out_sub)

        assert result is False
        assert not out_sub.exists()
        # Error output should appear in the warning log
        assert any("error: could not parse reference file" in r.getMessage() for r in caplog.records)

    def test_tmp_cleaned_on_failure(self, video: Path, in_sub: Path, out_sub: Path, cfg: MagicMock) -> None:
        """If tmp was partially created before a nonzero exit, it is deleted."""
        captured_tmp: list[Path] = []

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            # Create the tmp file but return nonzero
            tmp = Path(cmd[-1])
            captured_tmp.append(tmp)
            tmp.touch()
            return _FakePopen([], returncode=1)

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
        ):
            result = retime_subtitle(cfg, video, in_sub, out_sub)

        assert result is False
        # The partial tmp file must have been cleaned up.
        assert not captured_tmp[0].exists()


# ---------------------------------------------------------------------------
# Test: Cancellation
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX kill path only")
class TestCancellationPosix:
    def test_presignalled_cancel_returns_false_without_error_log(
        self,
        video: Path,
        in_sub: Path,
        out_sub: Path,
        cfg: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Pre-set cancel: process drains & exits before any kill → returns False.

        With cancel set before launch, the (mocked, instantly-finishing) process
        drains its stdout and is reaped before the watcher could ever kill it; the
        PID-reuse guard means no kill fires. The function must still return False
        via the post-run ``cancel_event.is_set()`` check, with no error log.
        """
        cancel_event = threading.Event()
        cancel_event.set()  # already cancelled before launch

        lines = ["info: analysing…"]

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            Path(cmd[-1]).touch()
            return _FakePopen(lines, returncode=0)

        import logging

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
            patch(_OS_GETPGID, return_value=99),
            patch(_OS_KILLPG),
            caplog.at_level(logging.WARNING, logger="anki_miner.services.subtitle_retimer"),
        ):
            result = retime_subtitle(cfg, video, in_sub, out_sub, cancel_event=cancel_event)

        # Cancel RETURN semantics: False, regardless of whether killpg fired.
        assert result is False
        # out_sub must not be written on a cancelled run.
        assert not out_sub.exists()
        # Cancel is not an error — no WARNING or ERROR records.
        error_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not error_records

    def test_cancel_while_process_alive_kills(
        self,
        video: Path,
        in_sub: Path,
        out_sub: Path,
        cfg: MagicMock,
    ) -> None:
        """Cancel arriving while the process is still streaming → os.killpg fires.

        Deterministic: the fake's stdout yields one line, fires cancel, then
        BLOCKS until the patched os.killpg signals it. This keeps the process
        "alive" (done_event not yet set) across the watcher's kill, so the
        process-alive guard genuinely exercises the kill path — no race.
        """
        cancel_event = threading.Event()
        killed_event = threading.Event()

        def _killpg(pgid: int, sig: int) -> None:
            # Watcher reached the kill — unblock the stdout iterator so the
            # main thread can finish draining and reap the process.
            killed_event.set()

        def _streaming_stdout() -> Any:
            # First line: trigger cancel, then wait for the watcher to kill.
            yield "info: analysing audio"
            cancel_event.set()
            # Block until the watcher's killpg fires (or a generous safety timeout
            # so the test can never hang).
            killed_event.wait(timeout=5.0)
            yield "shifted block 1 by 300ms"

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            fake = _FakePopen([], returncode=0, create_tmp=False)
            fake.stdout = _streaming_stdout()
            return fake

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
            patch(_OS_GETPGID, return_value=99),
            patch(_OS_KILLPG, side_effect=_killpg) as mock_killpg,
        ):
            result = retime_subtitle(cfg, video, in_sub, out_sub, cancel_event=cancel_event)

        # Result must be False (cancelled) and the kill must have fired.
        assert result is False
        mock_killpg.assert_called_once_with(99, signal.SIGKILL)
        assert killed_event.is_set()


# ---------------------------------------------------------------------------
# Test: AlassNotFoundError
# ---------------------------------------------------------------------------


class TestAlassNotFoundError:
    def test_file_not_found_raises_alass_not_found(
        self, video: Path, in_sub: Path, out_sub: Path, cfg: MagicMock
    ) -> None:
        """FileNotFoundError from Popen → AlassNotFoundError, nothing else raised."""

        def _factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
            raise FileNotFoundError("alass: No such file or directory")

        with (
            patch(_RESOLVE_ALASS, return_value="alass"),
            patch(_RESOLVE_FFMPEG, return_value="ffmpeg"),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_POPEN, side_effect=_factory),
            pytest.raises(AlassNotFoundError),
        ):
            retime_subtitle(cfg, video, in_sub, out_sub)

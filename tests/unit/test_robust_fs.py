from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from anki_miner.utils import robust_fs
from anki_miner.utils.robust_fs import robust_rmtree


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _no_jitter(delay: float) -> float:
    return delay


def test_raising_mode_retries_until_deadline_without_trailing_sleep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "locked"
    target.mkdir()
    clock = _FakeClock()
    error = OSError(errno.EBUSY, "busy")
    attempt_times: list[float] = []

    def always_busy(_target: Path, **_kwargs: Any) -> None:
        attempt_times.append(clock())
        raise error

    monkeypatch.setattr(robust_fs.shutil, "rmtree", always_busy)

    with pytest.raises(OSError) as exc_info:
        robust_rmtree(
            target,
            deadline_s=1.0,
            initial_backoff_s=0.1,
            max_backoff_s=1.0,
            jitter=_no_jitter,
            clock=clock,
            sleep=clock.sleep,
        )

    assert exc_info.value is error
    assert attempt_times == pytest.approx([0.0, 0.1, 0.3, 0.7, 1.0])
    assert clock.sleeps == pytest.approx([0.1, 0.2, 0.4, 0.3])
    assert len(clock.sleeps) == len(attempt_times) - 1


def test_non_lock_error_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "broken"
    target.mkdir()
    clock = _FakeClock()
    attempts = 0

    def fail_once(_target: Path, **_kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(robust_fs.shutil, "rmtree", fail_once)

    with pytest.raises(OSError, match="disk full"):
        robust_rmtree(target, clock=clock, sleep=clock.sleep)

    assert attempts == 1
    assert clock.sleeps == []


def test_windows_sharing_violation_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "locked"
    target.mkdir()
    clock = _FakeClock()
    error = OSError(errno.EIO, "sharing violation")
    error.winerror = 32  # type: ignore[attr-defined]
    attempts = 0

    def fail_once(_target: Path, **_kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error

    monkeypatch.setattr(robust_fs.shutil, "rmtree", fail_once)

    robust_rmtree(
        target,
        deadline_s=1.0,
        initial_backoff_s=0.1,
        jitter=_no_jitter,
        clock=clock,
        sleep=clock.sleep,
    )

    assert attempts == 2
    assert clock.sleeps == [0.1]


def test_outcome_mode_returns_error_without_masking_primary_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "locked"
    target.mkdir()
    cleanup_error = OSError(errno.EACCES, "cleanup locked")

    def fail_cleanup(_target: Path, **_kwargs: Any) -> None:
        raise cleanup_error

    monkeypatch.setattr(robust_fs.shutil, "rmtree", fail_cleanup)

    with pytest.raises(RuntimeError, match="primary"):
        try:
            raise RuntimeError("primary")
        finally:
            outcome = robust_rmtree(
                target,
                mode="outcome",
                deadline_s=0.0,
                clock=lambda: 0.0,
                sleep=lambda _seconds: None,
            )
            assert outcome == (False, cleanup_error)


def test_outcome_mode_reports_success(
    tmp_path: Path,
) -> None:
    target = tmp_path / "delete-me"
    target.mkdir()

    assert robust_rmtree(target, mode="outcome") == (True, None)
    assert not target.exists()


@pytest.mark.parametrize(
    ("version", "handler_name"),
    [
        ((3, 11), "onerror"),
        ((3, 12), "onexc"),
    ],
)
def test_rmtree_handler_dispatch_and_readonly_mode_preservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: tuple[int, int],
    handler_name: str,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    readonly = target / "readonly"
    readonly.write_bytes(b"x")
    os.chmod(readonly, 0o454)
    original_mode = stat.S_IMODE(readonly.stat().st_mode)
    retried_modes: list[int] = []
    kwargs_seen: dict[str, object] = {}

    def retry_func(path: str) -> None:
        retried_modes.append(stat.S_IMODE(Path(path).stat().st_mode))

    def fake_rmtree(_target: Path, **kwargs: object) -> None:
        kwargs_seen.update(kwargs)
        handler = kwargs[handler_name]
        assert isinstance(handler, Callable)
        error = PermissionError(errno.EACCES, "readonly", str(readonly))
        if handler_name == "onexc":
            handler(retry_func, str(readonly), error)
        else:
            handler(retry_func, str(readonly), (PermissionError, error, None))

    monkeypatch.setattr(robust_fs.sys, "version_info", version)
    monkeypatch.setattr(robust_fs.shutil, "rmtree", fake_rmtree)

    robust_rmtree(target)

    assert set(kwargs_seen) == {handler_name}
    assert retried_modes == [original_mode | stat.S_IWUSR]

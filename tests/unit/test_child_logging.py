"""The supervised ffsubsync child writes to a log sink of its own.

The child re-enters ``gui/launch.py`` with ``--ffsubsync-child`` and returns
before ``_install_early_crash_sink()``, so until now it logged nowhere: a child
that died before writing its JSON verdict left the parent reporting "sync did
nothing" with no record of why. These tests pin the sink, its independence from
the parent's file, and that failing to open it never costs the sync.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

CHILD_MODULE = "anki_miner.services.sync_engines._ffsubsync_child"


def _drop_child_sinks() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_anki_miner_child_sink", False):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture
def _restore_logging():
    """Clean slate before, and undo after.

    Before, because ``_install_child_log_sink`` is idempotent against the root
    logger: another test in the same xdist worker that dispatched the child
    branch would leave the sink installed and turn every assertion here into a
    no-op. After, because the handler and the process hooks are process-wide.
    """
    from anki_miner.utils import log_hooks

    root = logging.getLogger()
    before_level = root.level
    _drop_child_sinks()
    log_hooks.uninstall_process_log_hooks()
    try:
        yield
    finally:
        _drop_child_sinks()
        root.setLevel(before_level)
        log_hooks.uninstall_process_log_hooks()


def _dispatch_child(monkeypatch: pytest.MonkeyPatch, home: Path, body, argv=("--bogus",)) -> int:
    """Run ``launch.main()`` down the child branch with *body* as the child."""
    from anki_miner.gui import launch

    monkeypatch.setenv("ANKI_MINER_HOME", str(home))
    fake_child = ModuleType(CHILD_MODULE)
    fake_child.main = body  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, CHILD_MODULE, fake_child)
    monkeypatch.setattr(sys, "argv", ["anki_miner", launch.FFSUBSYNC_CHILD_FLAG, *argv])
    return launch.main()


def test_child_writes_its_own_log_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _restore_logging) -> None:
    from anki_miner.gui import launch

    def child(argv):
        logging.getLogger("x").warning("child probe")
        return 7

    assert _dispatch_child(monkeypatch, tmp_path, child) == 7

    text = (tmp_path / launch.CHILD_LOG_NAME).read_text(encoding="utf-8")
    assert "child probe" in text
    assert f"[pid {os.getpid()}]" in text


def test_child_sink_is_not_the_parents_handler_or_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _restore_logging
) -> None:
    """Two processes on one RotatingFileHandler lose records on rollover."""
    from anki_miner.gui import launch

    seen: list[list[logging.Handler]] = []

    def child(argv):
        seen.append(list(logging.getLogger().handlers))
        return 0

    _dispatch_child(monkeypatch, tmp_path, child)

    added = [h for h in seen[0] if getattr(h, "_anki_miner_child_sink", False)]
    assert len(added) == 1
    assert not getattr(added[0], "_anki_miner_sink", False)
    assert Path(added[0].baseFilename).name == launch.CHILD_LOG_NAME  # type: ignore[attr-defined]
    assert launch.CHILD_LOG_NAME != "anki_miner.log"
    assert not (tmp_path / "anki_miner.log").exists()


def test_child_sink_installs_the_process_log_hooks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _restore_logging
) -> None:
    """A thread or ``__del__`` failure in the child is otherwise printed nowhere."""
    from anki_miner.utils import log_hooks

    installed: list[bool] = []

    def child(argv):
        installed.append(log_hooks._INSTALLED)
        return 0

    _dispatch_child(monkeypatch, tmp_path, child)

    assert installed == [True]


def test_child_sink_is_installed_once_when_main_is_re_entered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _restore_logging
) -> None:
    counts: list[int] = []

    def child(argv):
        counts.append(sum(1 for h in logging.getLogger().handlers if getattr(h, "_anki_miner_child_sink", False)))
        return 0

    _dispatch_child(monkeypatch, tmp_path, child)
    _dispatch_child(monkeypatch, tmp_path, child)

    assert counts == [1, 1]


def test_a_sink_that_cannot_be_opened_never_costs_the_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _restore_logging
) -> None:
    monkeypatch.setattr(
        logging.handlers,
        "RotatingFileHandler",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no space left on device")),
    )

    assert _dispatch_child(monkeypatch, tmp_path, lambda argv: 5) == 5


def test_bundle_collects_the_name_this_module_writes() -> None:
    """T6 spells the name out rather than importing it; the two must agree."""
    from anki_miner.diagnostics import bundle
    from anki_miner.gui import launch

    assert bundle._CHILD_LOG_NAME == launch.CHILD_LOG_NAME

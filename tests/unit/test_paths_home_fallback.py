"""The relocated-home fallback must record why it fired.

A ``Path.home()`` failure silently moves every user file - config, logs,
dictionaries - into the system temp directory. Without a recorded reason the
only symptom is an empty-looking install, so ``HOME_FALLBACK_REASON`` carries
the erased exception for the session-start receipt to report.
"""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

import pytest

import anki_miner.config.paths as paths


def _reload_without_home(monkeypatch: pytest.MonkeyPatch, exc: Exception):
    def _raise() -> Path:
        raise exc

    monkeypatch.delenv("ANKI_MINER_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(_raise))
    return importlib.reload(paths)


def test_home_failure_records_the_reason_and_relocates(monkeypatch: pytest.MonkeyPatch):
    try:
        reloaded = _reload_without_home(monkeypatch, RuntimeError("no home"))

        assert reloaded.HOME_FALLBACK_REASON == "RuntimeError: no home"
        assert reloaded.ANKI_MINER_HOME.is_relative_to(Path(tempfile.gettempdir()))
    finally:
        monkeypatch.undo()
        importlib.reload(paths)


def test_a_working_home_leaves_no_fallback_reason(monkeypatch: pytest.MonkeyPatch):
    try:
        monkeypatch.delenv("ANKI_MINER_HOME", raising=False)
        reloaded = importlib.reload(paths)

        assert reloaded.HOME_FALLBACK_REASON is None
    finally:
        monkeypatch.undo()
        importlib.reload(paths)

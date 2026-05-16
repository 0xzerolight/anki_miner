"""Tests for PyInstaller env scrubbing at app startup."""

import os
import sys

from anki_miner.gui.app import _scrub_pyinstaller_env


def test_frozen_restores_orig(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/home/u/lib")

    _scrub_pyinstaller_env()

    assert os.environ["LD_LIBRARY_PATH"] == "/home/u/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in os.environ


def test_frozen_drops_when_no_orig(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/lib")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    _scrub_pyinstaller_env()

    assert "LD_LIBRARY_PATH" not in os.environ


def test_not_frozen_passthrough(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/home/u/lib")

    _scrub_pyinstaller_env()

    assert os.environ["LD_LIBRARY_PATH"] == "/bundle/lib"
    assert os.environ["LD_LIBRARY_PATH_ORIG"] == "/home/u/lib"


def test_frozen_handles_dyld_macos(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/bundle/lib")
    monkeypatch.setenv("DYLD_LIBRARY_PATH_ORIG", "/Users/u/lib")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    _scrub_pyinstaller_env()

    assert os.environ["DYLD_LIBRARY_PATH"] == "/Users/u/lib"
    assert "DYLD_LIBRARY_PATH_ORIG" not in os.environ

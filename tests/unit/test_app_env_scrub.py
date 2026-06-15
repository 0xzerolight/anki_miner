"""Tests for PyInstaller env scrubbing at app startup."""

import os
import sys

from anki_miner.gui.app import _force_software_video_decode, _scrub_pyinstaller_env

_HW_VAR = "QT_FFMPEG_DECODING_HW_DEVICE_TYPES"


def test_forces_software_decode_when_unset(monkeypatch):
    monkeypatch.delenv(_HW_VAR, raising=False)

    _force_software_video_decode()

    # Empty device-type list (",") disables all Qt FFmpeg HW decode backends.
    assert os.environ[_HW_VAR] == ","


def test_does_not_override_user_value(monkeypatch):
    monkeypatch.setenv(_HW_VAR, "cuda")

    _force_software_video_decode()

    assert os.environ[_HW_VAR] == "cuda"


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

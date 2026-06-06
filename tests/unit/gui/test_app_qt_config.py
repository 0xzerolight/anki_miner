"""Regression tests for Qt multimedia hardware-decode configuration.

Bug: on machines without a hardware AV1 decode path, Qt's ffmpeg multimedia
backend tried CUDA decode per-frame for the in-app preview player, flooding
stderr with "Failed setup for format cuda" / "Get current frame error" and
leaving the AV1 preview blank. _configure_qt_multimedia() disables hw decode
so Qt falls back to software decode.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from anki_miner.gui.app import _configure_qt_multimedia  # noqa: E402

_ENV_KEY = "QT_FFMPEG_DECODING_HW_DEVICE_TYPES"


def test_sets_software_decode_when_unset(monkeypatch):
    monkeypatch.delenv(_ENV_KEY, raising=False)

    _configure_qt_multimedia()

    # Empty device-type list ("," per Qt docs) disables all hw decode backends.
    assert os.environ[_ENV_KEY] == ","


def test_does_not_override_existing_value(monkeypatch):
    monkeypatch.setenv(_ENV_KEY, "cuda")

    _configure_qt_multimedia()

    # setdefault() must leave an explicit user override untouched.
    assert os.environ[_ENV_KEY] == "cuda"

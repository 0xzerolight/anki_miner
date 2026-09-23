"""Tests for whole-UI zoom injection (QT_SCALE_FACTOR) at app startup.

``_qt_scale_factor_set_by_app`` is the flag ``_relaunch_if_requested``
(tests/unit/test_app_restart.py) reads to tell "this process derived the var
from ui_zoom" apart from "a user set it externally" — only the former may be
popped before a restart's child process inherits our environment.
"""

import os
from dataclasses import replace

import pytest

from anki_miner.config import create_default_config
from anki_miner.gui import app as app_module
from anki_miner.gui.app import _apply_ui_zoom


@pytest.fixture(autouse=True)
def _reset_app_set_flag(monkeypatch):
    monkeypatch.setattr(app_module, "_qt_scale_factor_set_by_app", False)


def test_non_default_zoom_sets_scale_factor(monkeypatch):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    cfg = replace(create_default_config(), ui_zoom=1.5)

    _apply_ui_zoom(cfg)

    assert os.environ["QT_SCALE_FACTOR"] == "1.5"
    assert app_module._qt_scale_factor_set_by_app is True


def test_default_zoom_leaves_env_unset(monkeypatch):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    cfg = create_default_config()  # ui_zoom defaults to 1.0

    _apply_ui_zoom(cfg)

    assert "QT_SCALE_FACTOR" not in os.environ
    assert app_module._qt_scale_factor_set_by_app is False


def test_existing_env_override_is_not_clobbered(monkeypatch):
    monkeypatch.setenv("QT_SCALE_FACTOR", "1.25")
    cfg = replace(create_default_config(), ui_zoom=2.0)

    _apply_ui_zoom(cfg)

    # An explicit user-set env override wins over the config value.
    assert os.environ["QT_SCALE_FACTOR"] == "1.25"
    # And is never mistaken for one this process wrote itself.
    assert app_module._qt_scale_factor_set_by_app is False


def test_none_config_is_tolerated_and_leaves_env_unset(monkeypatch):
    # Startup config load can fail; _early_config falls back to None. Zoom must
    # skip silently rather than crash (NameError/AttributeError) the whole app.
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)

    _apply_ui_zoom(None)

    assert "QT_SCALE_FACTOR" not in os.environ
    assert app_module._qt_scale_factor_set_by_app is False

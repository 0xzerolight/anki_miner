"""Tests for whole-UI zoom injection (QT_SCALE_FACTOR) at app startup."""

import os
from dataclasses import replace

from anki_miner.config import create_default_config
from anki_miner.gui.app import _apply_ui_zoom


def test_non_default_zoom_sets_scale_factor(monkeypatch):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    cfg = replace(create_default_config(), ui_zoom=1.5)

    _apply_ui_zoom(cfg)

    assert os.environ["QT_SCALE_FACTOR"] == "1.5"


def test_default_zoom_leaves_env_unset(monkeypatch):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    cfg = create_default_config()  # ui_zoom defaults to 1.0

    _apply_ui_zoom(cfg)

    assert "QT_SCALE_FACTOR" not in os.environ


def test_existing_env_override_is_not_clobbered(monkeypatch):
    monkeypatch.setenv("QT_SCALE_FACTOR", "1.25")
    cfg = replace(create_default_config(), ui_zoom=2.0)

    _apply_ui_zoom(cfg)

    # An explicit user-set env override wins over the config value.
    assert os.environ["QT_SCALE_FACTOR"] == "1.25"


def test_none_config_is_tolerated_and_leaves_env_unset(monkeypatch):
    # Startup config load can fail; _early_config falls back to None. Zoom must
    # skip silently rather than crash (NameError/AttributeError) the whole app.
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)

    _apply_ui_zoom(None)

    assert "QT_SCALE_FACTOR" not in os.environ

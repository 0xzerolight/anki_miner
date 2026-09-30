"""D8: the Theme page left setup, so a fresh install's first theme follows the system."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from anki_miner.gui import app as app_module


def test_a_dark_system_starts_on_the_dark_theme():
    assert app_module._theme_for_color_scheme(Qt.ColorScheme.Dark) == "dark"


@pytest.mark.parametrize("scheme", [Qt.ColorScheme.Light, Qt.ColorScheme.Unknown])
def test_anything_else_starts_on_the_light_theme(scheme):
    assert app_module._theme_for_color_scheme(scheme) == "light"


def test_no_config_file_is_a_fresh_install(tmp_path):
    assert app_module._is_fresh_install(tmp_path / "gui_config.json") is True


def test_a_saved_config_is_not_a_fresh_install(tmp_path):
    (tmp_path / "gui_config.json").write_text("{}", encoding="utf-8")
    assert app_module._is_fresh_install(tmp_path / "gui_config.json") is False


def test_a_backup_alone_is_not_a_fresh_install(tmp_path):
    """A lost primary with a surviving .bak is recovered, not a new user."""
    (tmp_path / "gui_config.json.bak").write_text("{}", encoding="utf-8")
    assert app_module._is_fresh_install(tmp_path / "gui_config.json") is False

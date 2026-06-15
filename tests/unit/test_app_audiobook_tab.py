"""Smoke test: AudiobookTab is registered in the main() wiring (Issue #71).

Reuses the ``_build_tabs`` helper from ``test_app_deck_builder_tab`` (which
mirrors ``anki_miner.gui.app.main``'s tab-construction block) and asserts the
"Audiobook" tab is present, correctly typed, and ordered right after YouTube.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from tests.unit.test_app_deck_builder_tab import _build_tabs


@pytest.fixture
def wired_window(monkeypatch, test_config, qtbot):
    window, titles, tabs = _build_tabs(monkeypatch, test_config)
    qtbot.addWidget(window)
    yield window, titles, tabs
    window.deleteLater()


def test_audiobook_tab_present(wired_window):
    _window, titles, _tabs = wired_window
    assert "Audiobook" in titles


def test_audiobook_tab_is_correct_type(wired_window):
    _window, _titles, tabs = wired_window
    assert isinstance(tabs["Audiobook"], AudiobookTab)


def test_audiobook_tab_after_youtube(wired_window):
    """Audiobook must appear right after YouTube."""
    _window, titles, _tabs = wired_window
    assert titles.index("Audiobook") == titles.index("YouTube") + 1


def test_audiobook_tab_before_analytics(wired_window):
    """Audiobook must appear before Analytics."""
    _window, titles, _tabs = wired_window
    assert titles.index("Audiobook") < titles.index("Analytics")

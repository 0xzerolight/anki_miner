"""Smoke test: ReadingTab is registered in the main() wiring.

Reuses the ``_build_tabs`` helper from ``test_app_deck_builder_tab`` (which
mirrors ``anki_miner.gui.app.main``'s tab-construction block) and asserts the
"Reading" tab is present, correctly typed, and ordered right after Audio.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.reading_tab import ReadingTab
from tests.unit.test_app_deck_builder_tab import _build_tabs


@pytest.fixture
def wired_window(monkeypatch, test_config, qtbot):
    window, titles, tabs = _build_tabs(monkeypatch, test_config)
    qtbot.addWidget(window)
    yield window, titles, tabs
    window.deleteLater()


def test_reading_tab_present(wired_window):
    _window, titles, _tabs = wired_window
    assert "Reading" in titles


def test_reading_tab_is_correct_type(wired_window):
    _window, _titles, tabs = wired_window
    assert isinstance(tabs["Reading"], ReadingTab)


def test_reading_tab_after_audio(wired_window):
    """Reading must appear right after Audio."""
    _window, titles, _tabs = wired_window
    assert titles.index("Reading") == titles.index("Audio") + 1


def test_reading_tab_before_analytics(wired_window):
    """Reading must appear before Analytics."""
    _window, titles, _tabs = wired_window
    assert titles.index("Reading") < titles.index("Analytics")

"""Smoke test: AudiobookTab is registered in the main() wiring (Issue #71).

Uses the shared ``wired_window`` fixture (``tests/unit/conftest.py``), which
mirrors ``anki_miner.gui.app.main``'s tab-construction block, and asserts the
"Audio" tab is present, correctly typed, and ordered right after Deck Builder.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.audiobook_tab import AudiobookTab


def test_audiobook_tab_present(wired_window):
    _window, titles, _tabs = wired_window
    assert "Audio" in titles


def test_audiobook_tab_is_correct_type(wired_window):
    _window, _titles, tabs = wired_window
    assert isinstance(tabs["Audio"], AudiobookTab)


def test_audiobook_tab_after_deck_builder(wired_window):
    """Audio must appear right after Deck Builder."""
    _window, titles, _tabs = wired_window
    assert titles.index("Audio") == titles.index("Deck Builder") + 1


def test_audiobook_tab_before_analytics(wired_window):
    """Audio must appear before Analytics."""
    _window, titles, _tabs = wired_window
    assert titles.index("Audio") < titles.index("Analytics")

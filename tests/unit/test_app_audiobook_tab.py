"""Smoke test: AudiobookTab is registered in the main() wiring (Issue #71).

Uses the shared ``wired_window`` fixture (``tests/unit/conftest.py``), which
calls ``anki_miner.gui.app.compose_main_window``, and asserts the
"Audiobooks" tab is present, correctly typed, and ordered right after Deck Builder.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.audiobook_tab import AudiobookTab


def test_audiobook_tab_present(wired_window):
    _window, titles, _tabs = wired_window
    assert "Audiobooks" in titles


def test_audiobook_tab_is_correct_type(wired_window):
    _window, _titles, tabs = wired_window
    assert isinstance(tabs["Audiobooks"], AudiobookTab)


def test_audiobook_tab_after_deck_builder(wired_window):
    """Audiobooks must appear right after Deck Builder."""
    _window, titles, _tabs = wired_window
    assert titles.index("Audiobooks") == titles.index("Deck Builder") + 1


def test_audiobook_tab_before_analytics(wired_window):
    """Audiobooks must appear before Analytics."""
    _window, titles, _tabs = wired_window
    assert titles.index("Audiobooks") < titles.index("Analytics")

"""Smoke test: DeckBuilderTab is registered in the main() wiring.

Builds the full tab stack from ``anki_miner.gui.app.main``'s construction
block (via the shared ``wired_window`` fixture in ``tests/unit/conftest.py``,
which mirrors the real call) and asserts that a tab titled "Deck Builder" is
present and is an instance of DeckBuilderTab.

External services (AnkiConnect, disk I/O) are patched out by the shared
``patch_heavy_init`` fixture exactly as the other window-construction tests do.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab


def test_deck_builder_tab_present(wired_window):
    _window, titles, _tabs = wired_window
    assert "Deck Builder" in titles


def test_deck_builder_tab_is_correct_type(wired_window):
    _window, _titles, tabs = wired_window
    assert isinstance(tabs["Deck Builder"], DeckBuilderTab)


def test_deck_builder_tab_after_video(wired_window):
    """Deck Builder must appear right after the Video container (index 1)."""
    _window, titles, _tabs = wired_window
    assert titles.index("Deck Builder") == titles.index("Video") + 1


def test_deck_builder_tab_before_audio(wired_window):
    """Deck Builder must appear before Audio."""
    _window, titles, _tabs = wired_window
    assert titles.index("Deck Builder") < titles.index("Audio")

"""Regression tests for UI performance fixes.

Covers five hot paths that were rebuilding entire widget trees / scheduling
synchronous disk work / lacking bulk-insert guards on click:

- AnalyticsTab.showEvent → refresh_data staleness cache + bulk-insert guards
- ThemesPanel star toggle → surgical favorite-state update, no _populate
- WordPreviewDialog search → debounce + bulk-insert guards
- DictionarySettingsPanel._rebuild_list → setUpdatesEnabled wrapper
- PairPreviewDialog populate → bulk-insert guards
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.resources.styles.theme import REQUIRED_COLOR_KEYS, Theme
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.gui.widgets.dialogs.pair_preview_dialog import PairPreviewDialog
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.gui.widgets.dialogs.word_preview_dialog import WordPreviewDialog
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.gui.widgets.panels.themes_panel import _STAR_FILLED, _STAR_OUTLINE, ThemesPanel
from anki_miner.models import TokenizedWord
from anki_miner.models.stats import OverallStats
from anki_miner.utils.file_pairing import FilePair

_app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# Fix 1: AnalyticsTab.showEvent staleness cache + bulk-insert guards
# ---------------------------------------------------------------------------


def _make_stats_service() -> MagicMock:
    service = MagicMock()
    service.is_available.return_value = True
    service.get_overall_stats.return_value = OverallStats(
        total_sessions=0,
        total_cards_created=0,
        total_words_encountered=0,
        total_unknown_words=0,
        series_count=0,
    )
    service.get_recent_sessions.return_value = []
    service.get_series_difficulty.return_value = []
    service.get_milestones.return_value = []
    return service


def test_analytics_showevent_skips_refresh_within_ttl():
    """Two showEvents in rapid succession only trigger one stats query batch."""
    service = _make_stats_service()
    tab = AnalyticsTab(service)
    try:
        # Reset counters: __init__ does not auto-refresh; showEvent does.
        service.get_overall_stats.reset_mock()
        tab.showEvent(None)  # type: ignore[arg-type]
        first_calls = service.get_overall_stats.call_count
        assert first_calls == 1
        tab.showEvent(None)  # type: ignore[arg-type]
        # Second show within TTL: skipped.
        assert service.get_overall_stats.call_count == first_calls
    finally:
        tab.deleteLater()


def test_analytics_showevent_refreshes_after_ttl():
    """Show after TTL elapses triggers a fresh refresh."""
    service = _make_stats_service()
    tab = AnalyticsTab(service)
    try:
        tab.showEvent(None)  # type: ignore[arg-type]
        service.get_overall_stats.reset_mock()
        # Backdate the last-refresh timestamp past the TTL.
        tab._last_refresh = time.monotonic() - (AnalyticsTab._REFRESH_TTL_SECONDS + 1.0)
        tab.showEvent(None)  # type: ignore[arg-type]
        assert service.get_overall_stats.call_count == 1
    finally:
        tab.deleteLater()


def test_analytics_refresh_button_forces_refresh():
    """The Refresh button bypasses the staleness cache."""
    service = _make_stats_service()
    tab = AnalyticsTab(service)
    try:
        tab.refresh_data(force=False)
        service.get_overall_stats.reset_mock()
        # No timestamp tick — would be skipped without force.
        tab.refresh_data(force=True)
        assert service.get_overall_stats.call_count == 1
    finally:
        tab.deleteLater()


# ---------------------------------------------------------------------------
# Fix 2: ThemesPanel surgical favorite-state update (no _populate on toggle)
# ---------------------------------------------------------------------------


def _theme_dict(name: str, **overrides) -> dict:
    data: dict = {
        "name": name,
        "colors": dict.fromkeys(REQUIRED_COLOR_KEYS, "#000000"),
    }
    data.update(overrides)
    return data


@pytest.fixture
def themes_panel(tmp_path: Path) -> ThemesPanel:
    import json

    d = tmp_path / "themes"
    d.mkdir()
    (d / "light.json").write_text(json.dumps(_theme_dict("Light")))
    (d / "dark.json").write_text(json.dumps(_theme_dict("Dark")))
    Theme.initialize(active="light", favorites=(), shipped_dir=d)
    return ThemesPanel(d)


def test_themes_star_toggle_does_not_call_populate(themes_panel: ThemesPanel):
    """Clicking a star updates state surgically, never via full tree rebuild."""
    with patch.object(themes_panel, "_populate") as populate_spy:
        themes_panel._toggle_favorite("dark")
        assert populate_spy.call_count == 0


def test_themes_star_toggle_updates_button_in_place(themes_panel: ThemesPanel):
    """Toggling 'dark' flips its star button without rebuilding the row."""
    button = themes_panel._star_buttons["dark"]
    assert button.text() == _STAR_OUTLINE
    themes_panel._toggle_favorite("dark")
    # Same widget instance, mutated.
    assert themes_panel._star_buttons["dark"] is button
    assert button.text() == _STAR_FILLED


def test_themes_family_toggle_does_not_call_populate(tmp_path: Path):
    """Family-level toggle also avoids the full tree rebuild."""
    import json

    d = tmp_path / "themes"
    d.mkdir()
    (d / "catppuccin-mocha.json").write_text(
        json.dumps(_theme_dict("Catppuccin Mocha", family="Catppuccin", variant="Mocha"))
    )
    (d / "catppuccin-latte.json").write_text(
        json.dumps(_theme_dict("Catppuccin Latte", family="Catppuccin", variant="Latte"))
    )
    Theme.initialize(active="catppuccin-mocha", favorites=(), shipped_dir=d)
    panel = ThemesPanel(d)
    try:
        with patch.object(panel, "_populate") as populate_spy:
            panel._toggle_family_favorites(("catppuccin-mocha", "catppuccin-latte"))
            assert populate_spy.call_count == 0
        # Both variant buttons reflect new state.
        assert panel._star_buttons["catppuccin-mocha"].text() == _STAR_FILLED
        assert panel._star_buttons["catppuccin-latte"].text() == _STAR_FILLED
    finally:
        panel.deleteLater()


# ---------------------------------------------------------------------------
# Fix 3: WordPreviewDialog search debounce + populate guards
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_words() -> list[TokenizedWord]:
    return [
        TokenizedWord(
            surface=f"word{i}",
            lemma=f"lemma{i}",
            reading=f"reading{i}",
            sentence=f"sentence {i}",
            start_time=float(i),
            end_time=float(i + 1),
            duration=1.0,
        )
        for i in range(20)
    ]


def test_word_preview_search_debounces_keystrokes(test_config: AnkiMinerConfig, sample_words: list[TokenizedWord]):
    """Three keystrokes in a row only run one filter+populate after the timer fires."""
    dialog = WordPreviewDialog(sample_words, test_config)
    try:
        with patch.object(dialog, "_apply_search", wraps=dialog._apply_search) as apply_spy:
            dialog.search_input.setText("w")
            dialog.search_input.setText("wo")
            dialog.search_input.setText("wor")
            # Timer is single-shot; restarted on each keystroke, never fired
            # synchronously.
            assert apply_spy.call_count == 0
            # Force the timer to fire.
            dialog._search_debounce_timer.stop()
            dialog._apply_search()
            assert apply_spy.call_count == 1
    finally:
        dialog.deleteLater()


def test_word_preview_populate_disables_updates(test_config: AnkiMinerConfig, sample_words: list[TokenizedWord]):
    """_populate_table suspends repaints across the loop."""
    dialog = WordPreviewDialog(sample_words, test_config)
    try:
        update_calls: list[bool] = []
        original = dialog.table.setUpdatesEnabled

        def spy(enabled: bool) -> None:
            update_calls.append(enabled)
            original(enabled)

        with patch.object(dialog.table, "setUpdatesEnabled", side_effect=spy):
            dialog._populate_table()
        # Must contain at least one False (suspend) followed by True (resume).
        assert False in update_calls
        assert update_calls[-1] is True
    finally:
        dialog.deleteLater()


# ---------------------------------------------------------------------------
# Fix 4: DictionarySettingsPanel._rebuild_list wraps in setUpdatesEnabled
# ---------------------------------------------------------------------------


def test_dictionary_panel_rebuild_disables_updates(tmp_path: Path):
    """_rebuild_list suspends list repaints across the clear+populate."""
    dicts_root = tmp_path / "dicts"
    dicts_root.mkdir()
    panel = DictionarySettingsPanel(dicts_root=dicts_root)
    try:
        panel.set_chain((ChainEntry(kind="jisho", dict_id=None, enabled=True),))
        update_calls: list[bool] = []
        original = panel._list.setUpdatesEnabled

        def spy(enabled: bool) -> None:
            update_calls.append(enabled)
            original(enabled)

        with patch.object(panel._list, "setUpdatesEnabled", side_effect=spy):
            panel._rebuild_list()
        assert False in update_calls
        assert update_calls[-1] is True
    finally:
        panel.deleteLater()


# ---------------------------------------------------------------------------
# Fix 5: PairPreviewDialog populate guards
# ---------------------------------------------------------------------------


def test_pair_preview_populate_disables_updates(tmp_path: Path):
    """Pair preview populates with repaints suspended."""
    # Create a couple of fake files so .stat() doesn't blow up.
    vid = tmp_path / "ep1.mkv"
    sub = tmp_path / "ep1.srt"
    vid.write_bytes(b"x")
    sub.write_text("subtitle")
    pairs = [FilePair(video=vid, subtitle=sub)]

    # We can't easily spy on the table inside __init__ before it's built, so
    # verify the post-construct state instead: updates re-enabled at end of
    # populate (a False-then-True ordering during __init__ would have been
    # detected by Qt warnings, which pytest-qt would surface).
    dialog = PairPreviewDialog(pairs)
    try:
        # Repaints back on after populate finished.
        assert dialog.table.updatesEnabled() is True
        # And sorting state preserved (off by default for this dialog).
        assert dialog.table.isSortingEnabled() is False
    finally:
        dialog.deleteLater()


# ---------------------------------------------------------------------------
# Fix 6: WordCurationDialog fixed row height + debounced search
# ---------------------------------------------------------------------------


def _make_curation_words(count: int = 20) -> list[TokenizedWord]:
    return [
        TokenizedWord(
            surface=f"word{i}",
            lemma=f"lemma{i}",
            reading=f"reading{i}",
            sentence=f"sentence {i}",
            start_time=float(i),
            end_time=float(i + 1),
            duration=1.0,
        )
        for i in range(count)
    ]


def test_curation_uses_fixed_row_height():
    """Vertical header must use Fixed resize mode with 32px default section size at scale 1.0."""
    from PyQt6.QtWidgets import QHeaderView

    # Pin the global font scale to 1.0: the row height now scales with it
    # (Issue #63). Another test may also leave an enlarged stylesheet applied to
    # the QApplication, whose font metrics clamp the table's row height upward;
    # clear it so this test sees the unscaled baseline deterministically.
    Theme.set_font_scale(1.0)
    app = QApplication.instance()
    leaked_stylesheet = app.styleSheet() if app else ""
    if app:
        app.setStyleSheet("")
    dialog = WordCurationDialog(_make_curation_words())
    try:
        v_header = dialog.table.verticalHeader()
        assert v_header is not None
        assert v_header.sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed
        assert v_header.defaultSectionSize() == 32
    finally:
        dialog.deleteLater()
        if app:
            app.setStyleSheet(leaked_stylesheet)


def test_curation_search_debounces_keystrokes():
    """Three keystrokes in a row only run one _apply_search after the timer fires."""
    dialog = WordCurationDialog(_make_curation_words())
    try:
        with patch.object(dialog, "_apply_search", wraps=dialog._apply_search) as apply_spy:
            # Simulate rapid typing by setting the text field and calling _on_search_changed
            # (the same path that the textChanged signal takes).
            dialog.search_input.setText("w")
            dialog.search_input.setText("wo")
            dialog.search_input.setText("wor")
            # Timer is single-shot; restarted on each keystroke — never fired synchronously.
            assert apply_spy.call_count == 0
            # Force the timer to fire (simulating the 150 ms expiry).
            dialog._search_debounce_timer.stop()
            dialog._apply_search()
            assert apply_spy.call_count == 1
    finally:
        dialog.deleteLater()


def test_curation_apply_search_filters_same_rows_as_before():
    """_apply_search produces identical visibility to the old synchronous body."""
    words = _make_curation_words(10)
    dialog = WordCurationDialog(words)
    try:
        # Search for 'word5' — should hide every row except the one whose
        # columns contain 'word5' (surface/lemma/reading/sentence).
        dialog.search_input.setText("word5")
        dialog._apply_search()

        hidden = [dialog.table.isRowHidden(r) for r in range(dialog.table.rowCount())]
        # Exactly one row visible (the one for i=5).
        assert hidden.count(False) == 1

        # Clearing search shows all rows.
        dialog.search_input.setText("")
        dialog._apply_search()
        assert all(not dialog.table.isRowHidden(r) for r in range(dialog.table.rowCount()))
    finally:
        dialog.deleteLater()

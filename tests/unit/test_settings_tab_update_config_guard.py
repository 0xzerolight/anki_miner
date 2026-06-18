"""Tests for OVH-007: update_config dirty-state guard.

A config refresh that differs ONLY in externally-managed fields (theme, font
scale, first-run flags, update-banner fields) must NOT call _load_config() and
therefore must NOT clobber in-progress widget edits.  A refresh that touches a
panel-relevant field (e.g. dicts_root) must call _load_config() as before.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


class TestUpdateConfigGuard:
    """update_config must skip _load_config for external-only field changes."""

    def test_theme_only_change_preserves_unsaved_deck_edit(self, tab):
        """Editing a panel widget then calling update_config with theme-only diff
        must not reload the panel — the edit survives."""
        # Simulate unsaved edit: change the deck name widget directly.
        tab.anki_panel.deck_input.setText("MY_UNSAVED_DECK")

        # Incoming config differs only in theme.
        new_config = replace(tab.config, theme="dark")
        tab.update_config(new_config)

        # Widget must still show the unsaved value.
        assert tab.anki_panel.deck_input.text() == "MY_UNSAVED_DECK"

    def test_theme_favorites_only_preserves_unsaved_edit(self, tab):
        tab.anki_panel.deck_input.setText("UNSAVED")
        new_config = replace(tab.config, theme_favorites=("dark",))
        tab.update_config(new_config)
        assert tab.anki_panel.deck_input.text() == "UNSAVED"

    def test_ui_font_scale_only_preserves_unsaved_edit(self, tab):
        tab.anki_panel.deck_input.setText("UNSAVED")
        new_config = replace(tab.config, ui_font_scale=1.5)
        tab.update_config(new_config)
        assert tab.anki_panel.deck_input.text() == "UNSAVED"

    def test_skipped_update_version_only_preserves_unsaved_edit(self, tab):
        tab.anki_panel.deck_input.setText("UNSAVED")
        new_config = replace(tab.config, skipped_update_version="1.2.3")
        tab.update_config(new_config)
        assert tab.anki_panel.deck_input.text() == "UNSAVED"

    def test_last_known_version_only_preserves_unsaved_edit(self, tab):
        tab.anki_panel.deck_input.setText("UNSAVED")
        new_config = replace(tab.config, last_known_version="2.0.0")
        tab.update_config(new_config)
        assert tab.anki_panel.deck_input.text() == "UNSAVED"

    def test_first_run_shortcut_done_only_preserves_unsaved_edit(self, tab):
        tab.anki_panel.deck_input.setText("UNSAVED")
        new_config = replace(tab.config, first_run_shortcut_done=True)
        tab.update_config(new_config)
        assert tab.anki_panel.deck_input.text() == "UNSAVED"

    def test_first_run_setup_done_only_preserves_unsaved_edit(self, tab):
        tab.anki_panel.deck_input.setText("UNSAVED")
        new_config = replace(tab.config, first_run_setup_done=True)
        tab.update_config(new_config)
        assert tab.anki_panel.deck_input.text() == "UNSAVED"

    def test_multiple_external_fields_together_preserves_unsaved_edit(self, tab):
        """All external fields changing at once still skips reload."""
        tab.anki_panel.deck_input.setText("UNSAVED")
        new_config = replace(
            tab.config,
            theme="dark",
            ui_font_scale=1.2,
            skipped_update_version="9.9.9",
            first_run_shortcut_done=True,
        )
        tab.update_config(new_config)
        assert tab.anki_panel.deck_input.text() == "UNSAVED"

    def test_noop_refresh_skips_load_config(self, tab):
        """Identical config (no diff) must not reload either."""
        tab.anki_panel.deck_input.setText("UNSAVED")
        tab.update_config(tab.config)
        assert tab.anki_panel.deck_input.text() == "UNSAVED"

    def test_panel_field_change_triggers_reload(self, tab, tmp_path):
        """A config differing in a panel field (dicts_root) must call _load_config."""
        # Put something 'dirty' in the widget.
        tab.anki_panel.deck_input.setText("UNSAVED")

        # dicts_root is NOT in _EXTERNAL_ONLY_FIELDS — expect reload.
        new_root = tmp_path / "new_dicts"
        new_root.mkdir()
        new_config = replace(
            tab.config,
            anki_deck_name=tab.config.anki_deck_name,
            dicts_root=new_root,
        )
        tab.update_config(new_config)

        # After reload, deck_input should show the config value (not the unsaved edit).
        assert tab.anki_panel.deck_input.text() == tab.config.anki_deck_name

    def test_panel_field_plus_theme_change_triggers_reload(self, tab, tmp_path):
        """Mixed diff (external + panel field) must still reload."""
        tab.anki_panel.deck_input.setText("UNSAVED")
        new_root = tmp_path / "new_dicts2"
        new_root.mkdir()
        new_config = replace(
            tab.config,
            theme="dark",
            dicts_root=new_root,
        )
        tab.update_config(new_config)
        assert tab.anki_panel.deck_input.text() == tab.config.anki_deck_name

    def test_update_config_always_stores_new_config(self, tab):
        """self.config is always updated regardless of whether _load_config runs."""
        new_config = replace(tab.config, theme="dark")
        tab.update_config(new_config)
        assert tab.config.theme == "dark"

    def test_load_config_called_with_panel_change(self, tab, tmp_path):
        """Verify _load_config is invoked by checking the call via a spy."""
        load_calls = []
        original = tab._load_config

        def spy_load():
            load_calls.append(1)
            original()

        tab._load_config = spy_load

        new_root = tmp_path / "spy_dicts"
        new_root.mkdir()
        new_config = replace(tab.config, dicts_root=new_root)
        tab.update_config(new_config)
        assert load_calls, "_load_config must be called for a panel field change"

    def test_load_config_not_called_with_external_only_change(self, tab):
        """Verify _load_config is NOT invoked for external-only changes."""
        load_calls = []
        original = tab._load_config

        def spy_load():
            load_calls.append(1)
            original()

        tab._load_config = spy_load

        new_config = replace(tab.config, theme="dark")
        tab.update_config(new_config)
        assert not load_calls, "_load_config must NOT be called for external-only change"

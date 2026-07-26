"""External-config-reload repaint for the Settings panels.

Two families of settings used to survive a whole-config swap unrepainted
(Reset to Defaults, Import Settings, and any other ``update_config`` →
``config_refreshed`` fan-out):

* ``UISettingsPanel`` — outside ``SettingsTab._save_panels``, so only its
  language combo was re-synced; zoom, text size, native dialogs and the theme
  tree kept the previous config's values.
* ``FrequencySettingsPanel`` / ``PitchSettingsPanel`` /
  ``AudioPackSettingsPanel`` — captured their storage root at construction and
  never updated it, so a config with a different root left them scanning (and
  deleting from) the old directory.

The signal-safety tests are the load-bearing ones: the UI panel's change
handlers feed ``config_changed`` → ``MainWindow.update_config``, so an
unguarded widget mutation during a reload would write the panel's *stale*
state straight back into the config being loaded.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QTreeWidgetItem

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.panels.ui_settings_panel import UISettingsPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def ui_panel(test_config: AnkiMinerConfig, qtbot) -> UISettingsPanel:
    """A standalone UI panel constructed from the boot (``test_config``) values."""
    panel = UISettingsPanel(
        test_config.themes_root,
        test_config.ui_zoom,
        test_config.ui_language,
        test_config.use_native_file_dialogs,
    )
    qtbot.addWidget(panel)
    return panel


@pytest.fixture
def swapped_config(test_config: AnkiMinerConfig) -> AnkiMinerConfig:
    """A config whose every UI-panel field differs from ``test_config``."""
    return replace(
        test_config,
        ui_zoom=1.5,
        ui_language="ja",
        use_native_file_dialogs=True,
        theme="dark",
    )


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    """SettingsTab with a long debounce so no timer can fire mid-test."""
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    widget._debounce_timer.setInterval(60_000)
    yield widget
    widget.shutdown()
    for w in widget.iter_close_workers():
        if w is not None:
            w.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


def _record(*signals) -> list[tuple]:
    """Append every emission of ``signals`` to one shared list."""
    seen: list[tuple] = []
    for signal in signals:
        signal.connect(lambda *args, _s=signal: seen.append((_s, args)))
    return seen


def _active_theme_key(panel: UISettingsPanel) -> str | None:
    """Return the theme key of the tree row ``_populate`` marked current."""
    item = panel.tree.currentItem()
    if item is None:
        return None
    key = item.data(panel.COL_NAME, Qt.ItemDataRole.UserRole)
    return key if isinstance(key, str) else None


def _item_for_key(panel: UISettingsPanel, key: str) -> QTreeWidgetItem:
    """Return the (possibly nested) tree row carrying ``key``."""

    def walk(item: QTreeWidgetItem):
        yield item
        for i in range(item.childCount()):
            yield from walk(item.child(i))

    root = panel.tree.invisibleRootItem()
    assert root is not None
    for i in range(root.childCount()):
        for item in walk(root.child(i)):
            if item.data(panel.COL_NAME, Qt.ItemDataRole.UserRole) == key:
                return item
    raise AssertionError(f"no tree row for theme {key!r}")


class TestUIPanelLoadFromConfig:
    """Every UI-panel widget follows an external config swap."""

    def test_all_widgets_follow_the_swapped_config(self, ui_panel, swapped_config):
        # The caller re-seeds Theme before reloading; do the same here.
        Theme.set_mode(swapped_config.theme)
        Theme.set_font_scale(1.25)

        ui_panel.load_from_config(swapped_config)

        assert ui_panel.language_combo.currentData(Qt.ItemDataRole.UserRole) == "ja"
        assert ui_panel._ui_zoom == 1.5
        assert ui_panel.zoom_combo.currentData() == 150
        assert ui_panel.font_scale_combo.currentData() == 125
        assert ui_panel._use_native_file_dialogs is True
        assert ui_panel.native_dialogs_checkbox.isChecked() is True
        assert _active_theme_key(ui_panel) == "dark"

    def test_emits_nothing(self, ui_panel, swapped_config):
        """The regression that would silently corrupt the config being loaded."""
        Theme.set_mode(swapped_config.theme)
        Theme.set_font_scale(1.25)
        seen = _record(
            ui_panel.zoom_changed,
            ui_panel.font_scale_changed,
            ui_panel.native_dialogs_changed,
            ui_panel.language_changed,
            ui_panel.state_changed,
            ui_panel.favorites_changed,
        )

        ui_panel.load_from_config(swapped_config)

        assert seen == []

    def test_reverting_to_boot_values_repaints_back(self, ui_panel, test_config, swapped_config):
        ui_panel.load_from_config(swapped_config)
        ui_panel.load_from_config(test_config)

        assert ui_panel.language_combo.currentData(Qt.ItemDataRole.UserRole) == "en"
        assert ui_panel._ui_zoom == test_config.ui_zoom
        assert ui_panel.native_dialogs_checkbox.isChecked() is False


class TestUIPanelRestartNotes:
    """Restart notes track the values Qt is actually running with."""

    def test_hidden_when_loaded_value_equals_boot_value(self, ui_panel, test_config):
        # Both notes are already hidden at construction, so force them visible
        # first — otherwise this passes even if load_from_config did nothing.
        ui_panel.language_restart_note.setVisible(True)
        ui_panel.zoom_restart_note.setVisible(True)

        ui_panel.load_from_config(test_config)

        assert ui_panel.language_restart_note.isHidden()
        assert ui_panel.zoom_restart_note.isHidden()

    def test_visible_when_loaded_value_differs_from_boot_value(self, ui_panel, swapped_config):
        ui_panel.load_from_config(swapped_config)

        assert not ui_panel.language_restart_note.isHidden()
        assert not ui_panel.zoom_restart_note.isHidden()

    def test_round_trip_back_to_boot_clears_the_notes(self, ui_panel, test_config, swapped_config):
        ui_panel.load_from_config(swapped_config)
        assert not ui_panel.language_restart_note.isHidden()
        assert not ui_panel.zoom_restart_note.isHidden()

        ui_panel.load_from_config(test_config)

        assert ui_panel.language_restart_note.isHidden()
        assert ui_panel.zoom_restart_note.isHidden()

    def test_a_user_edit_still_reveals_the_note(self, ui_panel):
        """The handlers own the immediate reveal; load_from_config only re-syncs."""
        ui_panel._on_zoom_selected(ui_panel.zoom_combo.findData(150))

        assert not ui_panel.zoom_restart_note.isHidden()


class TestUIPanelRevertBaseline:
    """``load_from_config`` must not steal showEvent's first baseline capture."""

    def test_untouched_before_first_show(self, ui_panel, swapped_config):
        assert ui_panel._preview_baseline is None

        Theme.set_mode(swapped_config.theme)
        ui_panel.load_from_config(swapped_config)

        assert ui_panel._preview_baseline is None

    def test_repointed_at_the_new_theme_once_captured(self, ui_panel, swapped_config):
        ui_panel.reset_baseline()  # stands in for the first showEvent
        assert ui_panel._preview_baseline == "light"

        Theme.set_mode(swapped_config.theme)
        ui_panel.load_from_config(swapped_config)

        assert ui_panel._preview_baseline == "dark"

    def test_preview_survives_a_reload_triggered_by_an_unrelated_field(self, tab, test_config, swapped_config):
        """A mid-preview reload must not destroy the Revert target.

        The full live wiring, in four steps:

        1. showEvent captures the baseline.
        2. The user previews another theme. ``theme`` is in
           ``_EXTERNAL_ONLY_FIELDS`` so this round trip does NOT reload.
        3. The user toggles "Use system file dialogs" in the same panel.
           ``use_native_file_dialogs`` is not external-only, so the config round
           trip DOES reload the panel — carrying the previewed theme with it.
        4. Revert must still restore the theme from step 1.
        """
        # SettingsTab.config_changed → MainWindow.update_config →
        # config_refreshed → SettingsTab.update_config (app.py:950).
        tab.config_changed.connect(tab.update_config)
        Theme.set_mode("light")
        tab.config = replace(test_config, theme="light", use_native_file_dialogs=False)
        tab._load_config()
        panel = tab.ui_panel

        panel.showEvent(QShowEvent())
        assert panel._preview_baseline == "light"

        panel.tree.setCurrentItem(_item_for_key(panel, swapped_config.theme))
        assert Theme.get_current_mode() == "dark"
        assert tab.config.theme == "dark"  # persisted, but no reload
        assert panel._preview_baseline == "light"

        panel.native_dialogs_checkbox.setChecked(True)
        assert tab.config.use_native_file_dialogs is True  # this one DID reload
        assert panel.native_dialogs_checkbox.isChecked() is True

        assert panel._preview_baseline == "light"
        panel._revert_preview()
        assert Theme.get_current_mode() == "light"
        assert _active_theme_key(panel) == "light"


class TestUIPanelThemesRoot:
    """The "Open themes folder" button follows the config; the tree cannot."""

    def test_button_and_tooltip_follow_the_config(self, ui_panel, test_config, tmp_path):
        moved = tmp_path / "other-themes"

        ui_panel.load_from_config(replace(test_config, themes_root=moved))

        assert ui_panel._themes_root == moved
        assert str(moved) in ui_panel.open_folder_btn.toolTip()

    def test_open_uses_the_new_root(self, ui_panel, test_config, tmp_path, monkeypatch):
        moved = tmp_path / "other-themes"
        opened: list[str] = []
        monkeypatch.setattr(
            "anki_miner.gui.widgets.panels.ui_settings_panel.QDesktopServices.openUrl",
            lambda url: opened.append(url.toLocalFile()),
        )
        ui_panel.load_from_config(replace(test_config, themes_root=moved))

        ui_panel._open_themes_folder()

        assert opened == [str(moved)]


class TestChainPanelRoots:
    """The three chain panels re-root instead of scanning the old directory."""

    def test_frequency_panel_re_roots(self, tab, tmp_path):
        new_root = tmp_path / "other-freqs"
        tab.frequency_panel.set_freqs_root(new_root)

        assert tab.frequency_panel._freqs_root == new_root
        assert tab.frequency_panel._view is None

    def test_pitch_panel_re_roots(self, tab, tmp_path):
        new_root = tmp_path / "other-pitch"
        tab.pitch_panel.set_pitch_root(new_root)

        assert tab.pitch_panel._pitch_root == new_root
        assert tab.pitch_panel._view is None

    def test_audio_pack_panel_re_roots(self, tab, tmp_path):
        new_root = tmp_path / "other-packs"
        tab.audio_panel.set_packs_root(new_root)

        assert tab.audio_panel._packs_root == new_root
        assert tab.audio_panel._view is None


_CHAIN_PANELS = [
    ("frequency_panel", "set_freqs_root", "_freqs_root"),
    ("pitch_panel", "set_pitch_root", "_pitch_root"),
    ("audio_panel", "set_packs_root", "_packs_root"),
]


@pytest.mark.parametrize(("panel_attr", "setter", "root_attr"), _CHAIN_PANELS)
class TestChainPanelRootsAreIdempotent:
    """An unchanged root must not spawn a rescan.

    ``SettingsTab._load_config`` runs after every auto-save commit touching a
    non-external field, and hands each panel the same root nearly every time. A
    rescan there flashes a "Loading…" placeholder and holds a mutation token
    that disables the panel's Add button, for nothing.
    """

    def test_same_root_is_a_no_op(self, tab, panel_attr, setter, root_attr, monkeypatch):
        panel = getattr(tab, panel_attr)
        panel._view = object()
        scans: list[int] = []
        monkeypatch.setattr(panel, "_scan_and_render_async", lambda: scans.append(1))

        getattr(panel, setter)(getattr(panel, root_attr))

        assert scans == []
        assert panel._view is not None

    def test_a_changed_root_still_rescans(self, tab, tmp_path, panel_attr, setter, root_attr, monkeypatch):
        panel = getattr(tab, panel_attr)
        panel._view = object()
        scans: list[int] = []
        monkeypatch.setattr(panel, "_scan_and_render_async", lambda: scans.append(1))

        getattr(panel, setter)(tmp_path / "moved")

        assert scans == [1]
        assert panel._view is None


class TestSettingsTabLoadConfigFanOut:
    """``SettingsTab._load_config`` drives every new entry point."""

    def test_all_four_roots_follow_the_config(self, tab, test_config, tmp_path):
        moved = tmp_path / "moved"
        tab.config = replace(
            test_config,
            dicts_root=moved / "dicts",
            freqs_root=moved / "freqs",
            pitch_root=moved / "pitch",
            audio_packs_root=moved / "audio_packs",
        )

        tab._load_config()

        assert tab.dictionary_panel._dicts_root == moved / "dicts"
        assert tab.frequency_panel._freqs_root == moved / "freqs"
        assert tab.pitch_panel._pitch_root == moved / "pitch"
        assert tab.audio_panel._packs_root == moved / "audio_packs"

    def test_ui_panel_is_repainted(self, tab, swapped_config):
        Theme.set_mode(swapped_config.theme)
        tab.config = swapped_config

        tab._load_config()

        panel = tab.ui_panel
        assert panel.language_combo.currentData(Qt.ItemDataRole.UserRole) == "ja"
        assert panel.zoom_combo.currentData() == 150
        assert panel.native_dialogs_checkbox.isChecked() is True
        assert _active_theme_key(panel) == "dark"

    def test_reload_does_not_write_the_stale_panel_state_back(self, tab, swapped_config):
        """`ui_panel` signals reach `config_changed`; a reload must emit none."""
        Theme.set_mode(swapped_config.theme)
        tab.config = swapped_config
        seen = _record(tab.config_changed)

        tab._load_config()

        assert seen == []
        assert not tab._settings_dirty


def test_dicts_root_setter_still_syncs_its_selector(tab, tmp_path):
    """Guard the one behaviour the three new setters deliberately omit."""
    new_root = tmp_path / "selector-dicts"
    tab.dictionary_panel.set_dicts_root(new_root)

    assert Path(tab.dictionary_panel.dicts_root_selector.get_path()) == new_root

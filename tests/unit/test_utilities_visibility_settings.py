"""Settings -> Utilities: which tools the Utilities tab shows.

The checkboxes commit at once, the last checked one cannot be unchecked, and
every box is a jump target, which is where a hidden tool's Usage Guide entry
and task row lead.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import UTILITY_SUBTABS, utility_labels
from anki_miner.gui.widgets.panels.utilities_settings_panel import UtilitiesSettingsPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def panel(qtbot) -> UtilitiesSettingsPanel:
    widget = UtilitiesSettingsPanel()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    """SettingsTab with a long debounce so no timer can fire mid-test."""
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    widget._debounce_timer.setInterval(60_000)
    yield widget
    widget.shutdown()
    for worker in widget.iter_close_workers():
        if worker is not None:
            worker.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


def _emitted(signal) -> list[tuple]:
    seen: list[tuple] = []
    signal.connect(seen.append)
    return seen


class TestPanel:
    def test_one_box_per_tool_in_tab_order_with_the_tab_labels(self, panel):
        labels = utility_labels()

        assert list(panel.utility_checkboxes) == list(UTILITY_SUBTABS)
        assert [box.text() for box in panel.utility_checkboxes.values()] == [labels[k] for k in UTILITY_SUBTABS]
        assert all(box.isChecked() for box in panel.utility_checkboxes.values())

    def test_unchecking_emits_the_hidden_keys_in_tab_order(self, panel):
        seen = _emitted(panel.hidden_utilities_changed)

        panel.utility_checkboxes["mokuro"].setChecked(False)
        panel.utility_checkboxes["retime"].setChecked(False)

        assert seen == [("mokuro",), ("retime", "mokuro")]

    def test_the_last_checked_box_cannot_be_unchecked(self, panel):
        for key in UTILITY_SUBTABS[1:]:
            panel.utility_checkboxes[key].setChecked(False)
        last = panel.utility_checkboxes[UTILITY_SUBTABS[0]]

        assert last.isChecked() and not last.isEnabled()
        assert all(panel.utility_checkboxes[k].isEnabled() for k in UTILITY_SUBTABS[1:])

        panel.utility_checkboxes["retime"].setChecked(True)

        assert last.isEnabled()

    def test_load_from_config_repaints_without_emitting(self, panel, test_config):
        seen = _emitted(panel.hidden_utilities_changed)

        panel.load_from_config(replace(test_config, hidden_utilities=("retime", "no-such-tool")))

        assert seen == []
        assert not panel.utility_checkboxes["retime"].isChecked()
        assert all(panel.utility_checkboxes[k].isChecked() for k in UTILITY_SUBTABS if k != "retime")

    def test_a_config_hiding_every_tool_loads_as_none_hidden(self, panel, test_config):
        panel.load_from_config(replace(test_config, hidden_utilities=UTILITY_SUBTABS))

        assert all(box.isChecked() and box.isEnabled() for box in panel.utility_checkboxes.values())

    def test_a_load_leaving_one_tool_locks_its_box(self, panel, test_config):
        panel.load_from_config(replace(test_config, hidden_utilities=UTILITY_SUBTABS[1:]))

        assert not panel.utility_checkboxes[UTILITY_SUBTABS[0]].isEnabled()

    def test_every_box_is_anchored_by_its_tool_key(self, panel):
        by_id = {anchor.stable_id: anchor for anchor in panel.setting_anchors()}

        assert set(by_id) == {f"utilities.{key}" for key in UTILITY_SUBTABS}
        for key, box in panel.utility_checkboxes.items():
            assert by_id[f"utilities.{key}"].focus_widget is box


class TestSettingsTab:
    def test_a_toggle_commits_immediately(self, tab):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.utilities_panel.utility_checkboxes["retime"].setChecked(False)

        assert [config.hidden_utilities for config in received] == [("retime",)]
        assert not tab._debounce_timer.isActive()

    def test_a_toggle_keeps_a_pending_panel_edit(self, tab):
        tab.config_changed.connect(tab.update_config)
        tab.anki_panel.anki_tags_input.setText("pending-tag")

        tab.utilities_panel.utility_checkboxes["retime"].setChecked(False)

        assert tab.anki_panel.get_anki_tags() == "pending-tag"
        assert tab.config.hidden_utilities == ("retime",)

    def test_an_external_change_repaints_the_boxes(self, tab):
        """Not in _EXTERNAL_ONLY_FIELDS: a profile switch or import must reach the boxes."""
        tab.update_config(replace(tab.config, hidden_utilities=("download",)))

        assert not tab.utilities_panel.utility_checkboxes["download"].isChecked()

    def test_every_box_is_anchored_under_utilities(self, tab):
        by_id = {anchor.stable_id: anchor for anchor in tab.setting_anchors()}

        for key, box in tab.utilities_panel.utility_checkboxes.items():
            assert by_id[f"utilities.{key}"].focus_widget is box
        assert not any(stable_id.startswith("ui.utility_") for stable_id in by_id)

    def test_search_finds_a_tool_on_the_utilities_page(self, tab):
        from anki_miner.gui.widgets.settings_search import search

        hits = search(tab.setting_search_entries(), "Retime")

        assert "utilities.retime" in [entry.anchor.stable_id for entry in hits]
        assert all(entry.page_key != "ui" for entry in hits)

    def test_jumping_to_a_box_focuses_it(self, tab, qtbot):
        box = tab.utilities_panel.utility_checkboxes["retime"]
        tab.open_subtab("anki")

        tab.jump_to_setting("utilities.retime")

        qtbot.waitUntil(lambda: tab.focusWidget() is box, timeout=2000)
        assert tab.current_subtab_key() == "utilities"


def test_reset_to_defaults_restores_the_new_install_tool_set(test_config, qtbot, monkeypatch):
    """Not in _RESET_PRESERVE_UI: Reset returns the Utilities tab to the new-install set (D18)."""
    tab = SettingsTab(replace(test_config, hidden_utilities=("retime",)))
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "anki_miner.gui.widgets.settings_tab.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    received: list[AnkiMinerConfig] = []
    tab.config_changed.connect(received.append)

    tab._on_reset_to_defaults_clicked()

    assert received[-1].hidden_utilities == ("deckfilter", "download")
    assert tab.utilities_panel.utility_checkboxes["retime"].isChecked()


class TestMangaOcrLanguageGate:
    """E17: the Manga OCR box shows only for a language that can use the tool."""

    def test_hidden_for_another_language(self, panel, test_config):
        panel.load_from_config(replace(test_config, language="zh"))

        assert panel.utility_checkboxes["mokuro"].isHidden()
        assert not panel.utility_checkboxes["generate"].isHidden()

    def test_shown_for_japanese(self, panel, test_config):
        panel.load_from_config(replace(test_config, language="zh"))
        panel.load_from_config(replace(test_config, language="ja"))

        assert not panel.utility_checkboxes["mokuro"].isHidden()

    def test_the_last_visible_tool_stays_locked(self, panel, test_config):
        hidden = tuple(k for k in panel.utility_checkboxes if k not in ("generate", "mokuro"))
        panel.load_from_config(replace(test_config, language="zh", hidden_utilities=hidden))

        assert not panel.utility_checkboxes["generate"].isEnabled()

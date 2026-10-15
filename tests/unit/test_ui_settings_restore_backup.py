"""WP-13: Settings → General → Restore a backup… schedules a restore for the next launch."""

from __future__ import annotations

import json
from datetime import date

import pytest
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from anki_miner.gui.resources.styles.theme import REQUIRED_COLOR_KEYS, Theme
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.panels.ui_settings_panel import UISettingsPanel
from anki_miner.services.db_backup import backups_root, pending_restore, run_daily_backup


@pytest.fixture
def panel(qtbot, tmp_path) -> UISettingsPanel:
    themes = tmp_path / "themes"
    themes.mkdir()
    (themes / "light.json").write_text(
        json.dumps({"name": "Light", "colors": dict.fromkeys(REQUIRED_COLOR_KEYS, "#000000")}), encoding="utf-8"
    )
    Theme.initialize(active="light", favorites=(), shipped_dir=themes)
    widget = UISettingsPanel(themes)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def dialogs(monkeypatch):
    """The day picker returns ``state["item"]`` (first day when None); the confirm answers ``state["answer"]``."""
    state: dict = {"item": None, "answer": QMessageBox.StandardButton.Yes, "items": [], "questions": []}

    def _get_item(parent, title, label, items, current=0, editable=False):
        state["items"].append(list(items))
        choice = items[0] if state["item"] is None else state["item"]
        return choice, bool(choice)

    monkeypatch.setattr(QInputDialog, "getItem", _get_item)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: state["questions"].append(a[2]) or state["answer"])
    return state


def _backup(day: date) -> None:
    config_file = GUIConfigManager.CONFIG_FILE
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("{}", encoding="utf-8")
    run_daily_backup([config_file], backups_root(config_file), today=day)


def _pending() -> date | None:
    return pending_restore(backups_root(GUIConfigManager.CONFIG_FILE))


def test_no_backup_yet_is_a_banner(panel, dialogs):
    panel.restore_backup_btn.click()

    issue = panel.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "There is no backup yet. Anki Miner makes one each day when it starts."
    assert dialogs["items"] == []


def test_choosing_a_day_schedules_the_restore(panel, dialogs):
    _backup(date(2026, 10, 13))
    _backup(date(2026, 10, 14))
    dialogs["item"] = "2026-10-13"

    panel.restore_backup_btn.click()

    assert dialogs["items"] == [["2026-10-14", "2026-10-13"]]
    assert "2026-10-13" in dialogs["questions"][0]
    assert _pending() == date(2026, 10, 13)
    assert panel.restore_note.text() == "The backup from 2026-10-13 will be restored when Anki Miner next starts."
    assert not panel.restore_note.isHidden()
    assert not panel.restore_restart_btn.isHidden()


def test_declining_schedules_nothing(panel, dialogs):
    _backup(date(2026, 10, 14))
    dialogs["answer"] = QMessageBox.StandardButton.No

    panel.restore_backup_btn.click()

    assert _pending() is None
    assert panel.restore_note.isHidden()


def test_cancelling_the_picker_asks_nothing(panel, dialogs):
    _backup(date(2026, 10, 14))
    dialogs["item"] = ""

    panel.restore_backup_btn.click()

    assert dialogs["questions"] == []
    assert _pending() is None


def test_restore_is_found_by_settings_search(panel):
    anchor = next(a for a in panel.setting_anchors() if a.stable_id.endswith("restore_backup"))

    assert "Backups" in anchor.search_text()

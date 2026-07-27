"""Chain settings panels report scan and remove failures in place (D24).

A registry scan runs on first show of a Settings page. When it failed the panel
silently rendered rows without metadata and wrote one line to the log — the user
saw a list that looked fine and was not. The remove flow was the opposite
problem: a modal in the middle of a mutation the panel is still finishing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.config import ChainEntry
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.gui.widgets.panels.pitch_settings_panel import PitchSettingsPanel


@pytest.fixture
def panel(qtbot, tmp_path: Path) -> DictionarySettingsPanel:
    widget = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(widget)
    widget.set_chain((ChainEntry(kind="jisho", dict_id=None, enabled=True),))
    return widget


class TestScanFailure:
    def test_a_failed_scan_is_visible_on_the_panel(self, panel):
        panel._on_scan_error("sqlite3.DatabaseError: file is not a database")
        issue = panel.issue_banner().current_issue()
        assert issue is not None
        assert issue.summary == "Installed dictionaries could not be checked."
        assert "sqlite3" not in issue.summary
        assert "sqlite3.DatabaseError" in issue.details

    def test_the_rows_still_render_so_the_panel_is_not_stuck_loading(self, panel):
        panel._on_scan_error("boom")
        assert panel._list.count() == 1

    def test_the_repair_rescans(self, panel, monkeypatch):
        rescans: list[bool] = []
        monkeypatch.setattr(panel, "refresh_registry", lambda: rescans.append(True))
        panel._on_scan_error("boom")
        panel.issue_banner().action_button.click()
        assert rescans == [True]

    def test_a_successful_scan_clears_the_issue(self, panel):
        panel._on_scan_error("boom")
        panel._on_scan_done(None)
        assert panel.issue_banner().current_issue() is None

    @pytest.mark.parametrize(
        ("factory", "summary"),
        [
            (DictionarySettingsPanel, "Installed dictionaries could not be checked."),
            (FrequencySettingsPanel, "Installed frequency sources could not be checked."),
            (PitchSettingsPanel, "Installed pitch accent sources could not be checked."),
            (AudioPackSettingsPanel, "Installed audio packs could not be checked."),
        ],
    )
    def test_every_chain_panel_names_its_own_resource(self, qtbot, tmp_path, factory, summary):
        widget = factory(tmp_path)
        qtbot.addWidget(widget)
        widget._on_scan_error("boom")
        assert widget.issue_banner().current_issue().summary == summary


class TestRemoveFailure:
    def test_files_left_untouched_reports_without_the_path_in_the_sentence(self, panel):
        panel._warn_files_left(Path("/home/u/.anki_miner/dicts/jitendex"))
        issue = panel.issue_banner().current_issue()
        assert "/home/u" not in issue.summary
        assert "/home/u/.anki_miner/dicts/jitendex" in issue.details

    def test_a_post_save_refresh_failure_says_the_removal_is_durable(self, panel):
        panel._warn_post_save_failure("Jitendex", "refresh failed")
        issue = panel.issue_banner().current_issue()
        assert "refresh failed" not in issue.summary
        assert "refresh failed" in issue.details
        assert "Jitendex" in issue.summary

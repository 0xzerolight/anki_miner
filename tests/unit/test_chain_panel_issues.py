"""Chain settings panels report scan and remove failures in place (D24).

A registry scan runs on first show of a Settings page. When it failed the panel
silently rendered rows without metadata and wrote one line to the log — the user
saw a list that looked fine and was not. The remove flow was the opposite
problem: a modal in the middle of a mutation the panel is still finishing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AudioSourceEntry, ChainEntry, FreqEntry, PitchSourceEntry
from anki_miner.gui.utils.config_commit import ConfigCommitResult
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.gui.widgets.panels.pitch_settings_panel import PitchSettingsPanel
from anki_miner.services.audio_packs.registry import AudioPackMeta
from anki_miner.services.frequency.registry import FreqSourceMeta
from anki_miner.services.pitch_accent.registry import PitchSourceMeta


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


@pytest.mark.parametrize("kind", ["audio", "frequency", "pitch"])
def test_config_echo_retains_metadata_and_only_root_change_rescans(qtbot, tmp_path, monkeypatch, kind):
    index = tmp_path / "source" / "index.sqlite"
    if kind == "audio":
        widget = AudioPackSettingsPanel(tmp_path)
        entry = AudioSourceEntry(kind="pack", pack_id="source")
        meta = AudioPackMeta("source", "Source Name", "ajt", 12, True, tmp_path, True, index)
        set_root = widget.set_packs_root
    elif kind == "frequency":
        widget = FrequencySettingsPanel(tmp_path)
        entry = FreqEntry("source")
        meta = FreqSourceMeta("source", "Source Name", "csv", 12, True, 1, index)
        set_root = widget.set_freqs_root
    else:
        widget = PitchSettingsPanel(tmp_path)
        entry = PitchSourceEntry("source")
        meta = PitchSourceMeta("source", "Source Name", "csv", 12, True, 1, index)
        set_root = widget.set_pitch_root
    qtbot.addWidget(widget)
    widget.set_chain((entry,), registry_meta={"source": meta})

    set_root(tmp_path)
    widget.set_chain((entry,))

    assert widget._row_widget(0).title_label.full_text == "Source Name"
    scans: list[bool] = []
    monkeypatch.setattr(widget, "_scan_and_render_async", lambda: scans.append(True))
    set_root(tmp_path / "new-root")
    assert widget._view is None
    assert scans == [True]


def test_missing_audio_pack_is_visible_and_offers_reimport(qtbot, tmp_path):
    widget = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(widget)
    widget.set_chain(
        (AudioSourceEntry(kind="pack", pack_id="gone"),),
        registry_meta={},
    )
    requested: list[str] = []
    widget.reimport_pack_requested.connect(requested.append)

    row = widget._row_widget(0)
    assert row.warning_label.full_text == "⚠ pack missing — re-import"
    assert row.repair_button is not None
    row.repair_button.click()
    assert requested == ["gone"]


@pytest.mark.parametrize("factory", [FrequencySettingsPanel, PitchSettingsPanel])
def test_chain_only_remove_says_files_remain(qtbot, tmp_path, monkeypatch, factory):
    widget = factory(tmp_path)
    qtbot.addWidget(widget)
    bodies: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _parent, _title, body, *args: bodies.append(body) or QMessageBox.StandardButton.No,
    )

    assert "_confirm_chain_only_remove" in factory.__dict__
    assert widget._confirm_chain_only_remove("Source") is False
    assert bodies[0].endswith("No index files are deleted.")
    assert "Only the index files are deleted" not in bodies[0]


_FILES_LEFT = "The frequency source was removed from the chain; no files were deleted from disk."


def _post_save(name: str) -> str:
    return (
        f"{name} was removed, but Anki Miner could not refresh it. "
        "The removal is saved and will remain after a restart."
    )


class TestChainOnlyRemove:
    """Three removals leave the disk alone and must report alike.

    No managed folder (an empty source id), a folder that is already gone, and a
    folder the panel cannot prove it owns: each commits the smaller chain, then
    says what happened. Only the failure summary and what "files left" names
    differ between them.
    """

    @pytest.fixture
    def remove(self, qtbot, tmp_path, monkeypatch):
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        (foreign / "keep.txt").write_text("foreign", encoding="utf-8")
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)

        def run(source_id: str, commit: ConfigCommitResult):
            widget = FrequencySettingsPanel(tmp_path)
            qtbot.addWidget(widget)
            widget.set_chain((FreqEntry(source_id),), registry_meta={})
            widget.set_remove_chain_commit(lambda _chain: commit)
            shown: list[tuple[str, str]] = []
            real_show = widget.show_screen_issue

            def spy(issue, **kwargs):
                shown.append((issue.summary, issue.details))
                real_show(issue, **kwargs)

            monkeypatch.setattr(widget, "show_screen_issue", spy)
            widget.remove(0)
            qtbot.waitUntil(lambda: not widget.has_active_mutation(), timeout=3000)
            assert (foreign / "keep.txt").read_text(encoding="utf-8") == "foreign"
            return widget.get_chain(), shown

        return run

    @pytest.mark.parametrize("source_id", ["", "gone", "foreign"])
    def test_a_saved_removal_drops_the_entry_and_names_what_stayed(self, remove, tmp_path, source_id):
        chain, shown = remove(source_id, ConfigCommitResult.committed())

        assert chain == ()
        assert (
            shown
            == {
                "": [(_FILES_LEFT, "(missing)")],
                "gone": [],
                "foreign": [(_FILES_LEFT, str(tmp_path.resolve() / "foreign"))],
            }[source_id]
        )

    @pytest.mark.parametrize("source_id", ["", "gone", "foreign"])
    def test_a_refresh_failure_warns_before_the_files_left_notice(self, remove, tmp_path, source_id):
        chain, shown = remove(source_id, ConfigCommitResult.post_save_failure(RuntimeError("refresh failed")))

        post_save = (_post_save(source_id or "(missing)"), "refresh failed")
        assert chain == ()
        assert (
            shown
            == {
                "": [post_save, (_FILES_LEFT, "(missing)")],
                "gone": [post_save],
                "foreign": [post_save, (_FILES_LEFT, str(tmp_path.resolve() / "foreign"))],
            }[source_id]
        )

    @pytest.mark.parametrize("source_id", ["", "gone", "foreign"])
    def test_a_failed_save_keeps_the_entry_and_says_why(self, remove, tmp_path, source_id):
        chain, shown = remove(source_id, ConfigCommitResult.pre_save_failure(RuntimeError("disk full")))

        not_saved = "%s could not be removed: its settings could not be saved. Restart Anki Miner and try again."
        root = tmp_path.resolve()
        assert chain == (FreqEntry(source_id),)
        assert (
            shown
            == {
                "": [(not_saved % "(missing)", "(missing): disk full")],
                "gone": [(not_saved % "gone", f"{root / 'gone'}: disk full")],
                "foreign": [
                    (
                        "foreign could not be removed. Its files are intact — try again.",
                        f"{root / 'foreign'}: disk full",
                    )
                ],
            }[source_id]
        )

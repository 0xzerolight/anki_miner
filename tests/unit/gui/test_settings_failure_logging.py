"""The silent refusals around settings, queue restore, probes and confirmations.

Every arm covered here used to ``return`` without a word: a settings commit the
mutation preflight refused, rows a queue snapshot dropped on restore, a language
whose engine probe raised, and a destructive action the user declined. The
support reports they produce ("settings don't stick", "restore lost my items",
"language vanished", "Delete did nothing") are indistinguishable from a bug
until the log says which arm ran.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import queue_state_store
from anki_miner.gui.widgets.panels import mining_language_settings_panel
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    """SettingsTab with a long debounce so the test controls commit timing."""
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    widget._debounce_timer.setInterval(60_000)
    yield widget
    widget.shutdown()
    for worker in widget.iter_close_workers():
        if worker is not None:
            worker.wait(3000)


class TestSettingsFlushRefused:
    """``commit_pending_settings_for_mutation`` names the arm it refused on."""

    def test_commit_raised_logs_a_warning(self, test_config, qtbot, caplog):
        def explode(_config):
            raise RuntimeError("commit blew up")

        widget = SettingsTab(test_config, commit_config=explode)
        qtbot.addWidget(widget)
        widget._debounce_timer.setInterval(60_000)
        try:
            widget.anki_panel.set_deck_name("RefusedDeck")
            assert widget._settings_dirty is True
            with caplog.at_level(logging.WARNING, logger="anki_miner.gui.widgets.settings_tab"):
                assert widget.commit_pending_settings_for_mutation() is False
        finally:
            widget.shutdown()
            for worker in widget.iter_close_workers():
                if worker is not None:
                    worker.wait(3000)

        line = next(r.getMessage() for r in caplog.records if "Settings flush refused" in r.getMessage())
        assert "reason=commit_raised" in line
        assert "RuntimeError" in line

    def test_active_mutation_logs_a_warning(self, tab, tmp_path, caplog):
        new_root = tmp_path / "new-root"
        new_root.mkdir()
        tab.dictionary_panel.dicts_root_selector.set_path(str(new_root))
        token = tab.dictionary_panel.hold_mutation("import")
        try:
            with caplog.at_level(logging.WARNING, logger="anki_miner.gui.widgets.settings_tab"):
                assert tab.commit_pending_settings_for_mutation() is False
        finally:
            tab.dictionary_panel.release(token)

        line = next(r.getMessage() for r in caplog.records if "Settings flush refused" in r.getMessage())
        assert "reason=active_mutation" in line


class TestSubtitleRegexRejected:
    """A reverted regex names the pattern that was thrown away."""

    def test_commit_logs_the_rejected_pattern(self, tab, caplog):
        tab.filtering_panel.set_subtitle_regex_filter("([unclosed")
        with caplog.at_level(logging.WARNING, logger="anki_miner.gui.widgets.settings_tab"):
            tab.commit_settings()

        line = next(r.getMessage() for r in caplog.records if "Subtitle regex rejected" in r.getMessage())
        assert "([unclosed" in line
        assert "error=" in line


@pytest.fixture
def queue_home(tmp_path, monkeypatch):
    """Retarget the runtime-state root so snapshots land under ``tmp_path``."""
    from anki_miner.config import paths
    from anki_miner.gui.utils.config_manager import GUIConfigManager

    home = tmp_path / "home"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", home / "gui_config.json")
    monkeypatch.setattr(paths, "ANKI_MINER_HOME", home)
    return home


class TestQueueRestoreDroppedRows:
    """A snapshot that loses rows on restore says how many and from where."""

    def _write(self, rows: list[dict], key: str = "queue.youtube") -> None:
        path = queue_state_store.snapshot_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": queue_state_store.SCHEMA_VERSION, "key": key, "items": rows}),
            encoding="utf-8",
        )

    def _youtube_row(self, item_id: str, url: str) -> dict:
        return {
            "id": item_id,
            "source": {"kind": queue_state_store.SOURCE_URL, "url": url, "title": "t"},
            "status": queue_state_store.STATUS_INTERRUPTED,
        }

    def test_one_bogus_row_logs_kept_and_dropped(self, queue_home, caplog):
        self._write(
            [
                self._youtube_row("a", "https://y/1"),
                {"id": "b", "source": {"kind": "nonsense"}, "status": "ready"},
                self._youtube_row("c", "https://y/2"),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="anki_miner.gui.utils.queue_state_store"):
            snapshot = queue_state_store.load("queue.youtube")

        assert snapshot is not None
        assert len(snapshot.items) == 2
        line = next(r.getMessage() for r in caplog.records if "Queue restore dropped rows" in r.getMessage())
        assert "key=queue.youtube" in line
        assert "kept=2" in line
        assert "dropped=1" in line

    def test_clean_snapshot_logs_nothing(self, queue_home, caplog):
        self._write([self._youtube_row("a", "https://y/1")])

        with caplog.at_level(logging.WARNING, logger="anki_miner.gui.utils.queue_state_store"):
            assert queue_state_store.load("queue.youtube") is not None

        assert not [r for r in caplog.records if "Queue restore dropped rows" in r.getMessage()]

    def test_version_mismatch_logs_a_debug_reason(self, queue_home, caplog):
        path = queue_state_store.snapshot_path("queue.youtube")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": -1, "key": "queue.youtube", "items": []}), encoding="utf-8")

        with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.queue_state_store"):
            assert queue_state_store.load("queue.youtube") is None

        line = next(r.getMessage() for r in caplog.records if "Queue restore skipped" in r.getMessage())
        assert "reason=version_mismatch" in line


class TestLanguagePickerProbe:
    """A picker row whose engine probe raised names the language it dropped."""

    def test_find_spec_failure_logs_a_warning(self, caplog, monkeypatch):
        from anki_miner.languages.pack_spec import LanguagePack, PackComponent

        pack = LanguagePack(
            code="zh",
            approx_download_mb=1,
            components=(PackComponent(import_name="jieba", required=True, sentinels=()),),
        )

        def explode(_name):
            raise ValueError("bad module name")

        monkeypatch.setattr("importlib.util.find_spec", explode)

        with caplog.at_level(logging.WARNING, logger="anki_miner.gui.widgets.panels.mining_language_settings_panel"):
            assert mining_language_settings_panel.pack_already_importable(pack) is False

        line = next(r.getMessage() for r in caplog.records if "Language unavailable in picker" in r.getMessage())
        assert "code=zh" in line
        assert "ValueError" in line


class TestConfirmationAnswers:
    """A declined destructive confirmation leaves a receipt."""

    def test_declined_reset_logs_no(self, tab, caplog, monkeypatch):
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

        with caplog.at_level(logging.INFO, logger="anki_miner.gui.widgets.settings_tab"):
            tab._on_reset_to_defaults_clicked()

        line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Confirm:"))
        assert "action=reset_settings" in line
        assert "answer=no" in line

    def test_accepted_reset_logs_yes(self, tab, caplog, monkeypatch):
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        tab.config = replace(tab.config)

        with caplog.at_level(logging.INFO, logger="anki_miner.gui.widgets.settings_tab"):
            tab._on_reset_to_defaults_clicked()

        line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Confirm:"))
        assert "action=reset_settings" in line
        assert "answer=yes" in line

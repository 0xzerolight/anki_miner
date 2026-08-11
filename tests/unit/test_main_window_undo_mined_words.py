"""Tests for OVH-030: Undo reverts source='mined' known-words rows.

When the user clicks Undo after a mining run, the undo_callback in
_on_run_details must revert the session's source='mined' rows from
known_words.db so those words are re-mineable on the next run.

Issue #42 invariant: source='user' and source='anki' rows are NEVER touched.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.models import ProcessingResult


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    """MainWindow with heavy startup effects stubbed out."""
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def _capture_undo_callback(monkeypatch) -> dict:
    """Patch ResultsDialog so the undo_callback is captured without running exec()."""
    from anki_miner.gui import main_window as mw_module

    captured: dict = {}

    class _FakeDialog:
        undo_completed = False

        def __init__(self, result, parent, undo_callback=None, on_undo_committed=None):
            captured["cb"] = undo_callback

        def exec(self):
            return 0

    monkeypatch.setattr(mw_module, "ResultsDialog", _FakeDialog)
    return captured


def _fake_delete_notes(monkeypatch, deleted_count: int = 1) -> None:
    """Make AnkiService.delete_notes return ``deleted_count`` without networking."""
    from anki_miner.gui import main_window as mw_module

    original_class = mw_module.AnkiService

    class _FakeAnki(original_class):
        def __init__(self, config):
            # Don't call super().__init__ (avoids real network setup)
            self._config = config

        def delete_notes(self, note_ids):
            return deleted_count

    monkeypatch.setattr(mw_module, "AnkiService", _FakeAnki)


class TestUndoRevertsMinedWords:
    """Undo removes source='mined' rows but never source='user' or 'anki' (OVH-030)."""

    def _setup_known_words_db(self, db_path: Path, words: dict[str, str]) -> None:
        """Seed the known_words DB with {lemma: source} entries."""
        from anki_miner.services.known_word_db import KnownWordDB

        db = KnownWordDB(db_path)
        db.initialize()
        for lemma, source in words.items():
            db.add_words({lemma}, source=source)

    def test_undo_removes_mined_forms_from_known_words_db(self, main_window, monkeypatch, test_config, tmp_path):
        """After undo, source='mined' rows from the session are gone so those words
        can be re-mined on the next run."""
        from anki_miner.services.known_word_db import KnownWordDB

        db_path = test_config.known_words_db_path
        self._setup_known_words_db(db_path, {"食べる": "mined", "走る": "mined"})

        captured = _capture_undo_callback(monkeypatch)
        _fake_delete_notes(monkeypatch, deleted_count=2)

        # use_known_words_db must be True for the revert path to run.
        config_with_kwdb = replace(main_window.config, use_known_words_db=True)
        main_window.update_config(config_with_kwdb)

        result = ProcessingResult(
            total_words_found=2,
            new_words_found=2,
            cards_created=2,
            card_ids=[10, 11],
            mined_forms=["食べる", "走る"],
        )
        main_window._on_run_details(result)
        captured["cb"]([10, 11])

        db = KnownWordDB(db_path)
        assert db.get_known_words() == set(), "mined rows should be removed after undo"

    def test_undo_does_not_touch_user_rows(self, main_window, monkeypatch, test_config):
        """source='user' rows are NEVER removed by the undo path (Issue #42)."""
        from anki_miner.services.known_word_db import KnownWordDB

        db_path = test_config.known_words_db_path
        # 食べる is mined (from this session); ラーメン is user-curated.
        self._setup_known_words_db(db_path, {"食べる": "mined", "ラーメン": "user"})

        captured = _capture_undo_callback(monkeypatch)
        _fake_delete_notes(monkeypatch, deleted_count=1)

        config_with_kwdb = replace(main_window.config, use_known_words_db=True)
        main_window.update_config(config_with_kwdb)

        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[20],
            mined_forms=["食べる"],
        )
        main_window._on_run_details(result)
        captured["cb"]([20])

        db = KnownWordDB(db_path)
        assert db.get_words_by_source("user") == {"ラーメン"}, "user row must survive undo"
        assert "食べる" not in db.get_known_words(), "mined row must be removed"

    def test_undo_does_not_touch_anki_rows(self, main_window, monkeypatch, test_config):
        """source='anki' rows (from prior Anki sync) are left alone by undo."""
        from anki_miner.services.known_word_db import KnownWordDB

        db_path = test_config.known_words_db_path
        # 飲む was already known via Anki sync; 走る was mined this session.
        self._setup_known_words_db(db_path, {"飲む": "anki", "走る": "mined"})

        captured = _capture_undo_callback(monkeypatch)
        _fake_delete_notes(monkeypatch, deleted_count=1)

        config_with_kwdb = replace(main_window.config, use_known_words_db=True)
        main_window.update_config(config_with_kwdb)

        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[30],
            mined_forms=["走る"],
        )
        main_window._on_run_details(result)
        captured["cb"]([30])

        db = KnownWordDB(db_path)
        assert db.get_known_words() == {"飲む"}, "anki row must survive undo"

    def test_undo_reverts_mined_rows_even_when_toggle_off(self, main_window, monkeypatch, test_config):
        """Revert is gated on the DB existing, NOT on use_known_words_db (F2).

        The mining write records source='mined' rows whenever the DB file exists,
        regardless of the toggle, so undo must revert under the same condition —
        otherwise an existing DB accrues orphaned 'mined' rows that suppress
        re-mining if the toggle is later turned on.
        """
        from anki_miner.services.known_word_db import KnownWordDB

        db_path = test_config.known_words_db_path
        self._setup_known_words_db(db_path, {"食べる": "mined", "ラーメン": "user"})

        captured = _capture_undo_callback(monkeypatch)
        _fake_delete_notes(monkeypatch, deleted_count=1)

        config_no_kwdb = replace(main_window.config, use_known_words_db=False)
        main_window.update_config(config_no_kwdb)

        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[40],
            mined_forms=["食べる"],
        )
        main_window._on_run_details(result)
        captured["cb"]([40])

        db = KnownWordDB(db_path)
        # Mined row reverted even with the toggle off; user row untouched.
        assert "食べる" not in db.get_known_words(), "mined row must be reverted regardless of toggle"
        assert db.get_words_by_source("user") == {"ラーメン"}, "user row must survive"

    def test_undo_noop_when_db_absent(self, main_window, monkeypatch, test_config, tmp_path):
        """When no known_words.db exists, undo's revert is a no-op (no DB created)."""
        absent_db = tmp_path / "nonexistent" / "known_words.db"

        captured = _capture_undo_callback(monkeypatch)
        _fake_delete_notes(monkeypatch, deleted_count=1)

        config = replace(main_window.config, known_words_db_path=absent_db)
        main_window.update_config(config)

        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[50],
            mined_forms=["食べる"],
        )
        main_window._on_run_details(result)
        # Must not raise, must not create the DB file (is_available() guard).
        captured["cb"]([50])

        assert not absent_db.exists(), "revert must not create a DB when none exists"

    def test_undo_skipped_when_mined_forms_empty(self, main_window, monkeypatch, test_config):
        """When result.mined_forms is empty, the known_words.db revert is skipped."""
        from anki_miner.services.known_word_db import KnownWordDB

        db_path = test_config.known_words_db_path
        self._setup_known_words_db(db_path, {"食べる": "mined"})

        captured = _capture_undo_callback(monkeypatch)
        _fake_delete_notes(monkeypatch, deleted_count=0)

        config_with_kwdb = replace(main_window.config, use_known_words_db=True)
        main_window.update_config(config_with_kwdb)

        result = ProcessingResult(
            total_words_found=0,
            new_words_found=0,
            cards_created=0,
            card_ids=[],
            mined_forms=[],  # empty — nothing to revert
        )
        main_window._on_run_details(result)
        captured["cb"]([])

        db = KnownWordDB(db_path)
        # Word should still be present — revert was skipped.
        assert db.get_known_words() == {"食べる"}

    def test_undo_db_failure_does_not_crash(self, main_window, monkeypatch, test_config):
        """A known_words.db failure during undo must be swallowed, not raised."""
        from unittest.mock import patch

        from anki_miner.services import known_word_db as kw_module

        captured = _capture_undo_callback(monkeypatch)
        _fake_delete_notes(monkeypatch, deleted_count=1)

        config_with_kwdb = replace(main_window.config, use_known_words_db=True)
        main_window.update_config(config_with_kwdb)

        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[50],
            mined_forms=["食べる"],
        )
        main_window._on_run_details(result)

        with patch.object(kw_module.KnownWordDB, "remove_words", side_effect=Exception("db blow-up")):
            # Must not raise — undo must succeed even if known_words.db revert fails.
            deleted = captured["cb"]([50])

        assert deleted == 1

    def test_successful_undo_consumes_originating_inline_receipt(self, main_window, monkeypatch, test_config, qtbot):
        from anki_miner.gui import main_window as mw_module
        from anki_miner.gui.controllers.run_receipt import RunReceipt
        from anki_miner.gui.utils.progress_telemetry import ActiveDuration
        from anki_miner.gui.widgets.inline_receipt import InlineReceipt
        from anki_miner.models import TerminalOutcome
        from anki_miner.services.known_word_db import KnownWordDB

        db_path = test_config.known_words_db_path
        self._setup_known_words_db(db_path, {"猫": "mined"})
        _fake_delete_notes(monkeypatch, deleted_count=1)
        main_window.update_config(replace(main_window.config, use_known_words_db=True))

        dialogs: list[dict] = []

        class _FakeDialog:
            def __init__(self, result, parent, undo_callback=None, on_undo_committed=None):
                dialogs.append(
                    {
                        "undo_callback": undo_callback,
                        "on_undo_committed": on_undo_committed,
                    }
                )

            def exec(self):
                return 0

        monkeypatch.setattr(mw_module, "ResultsDialog", _FakeDialog)
        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[101],
            mined_forms=["猫"],
        )
        receipt = InlineReceipt()
        qtbot.addWidget(receipt)
        receipt.show_receipt(
            RunReceipt(
                outcome=TerminalOutcome.SUCCESS,
                items_total=1,
                items_completed=1,
                items_failed=0,
                notes_added=1,
                note_ids=(101,),
                duration=ActiveDuration(active_s=1.0, suspended_s=0.0),
                results=(result,),
            )
        )

        def open_details():
            current = receipt.receipt
            aggregate = current.aggregate_result() if current is not None else None
            if aggregate is not None:
                main_window._on_run_details(aggregate)

        receipt.details_requested.connect(open_details)
        receipt.details_button.click()
        deleted = dialogs[0]["undo_callback"]([101])
        dialogs[0]["on_undo_committed"](deleted)

        db = KnownWordDB(db_path)
        db.add_words({"猫"}, source="mined")
        receipt.details_button.click()
        if len(dialogs) > 1:
            deleted = dialogs[1]["undo_callback"]([101])
            dialogs[1]["on_undo_committed"](deleted)

        assert len(dialogs) == 1
        assert db.get_words_by_source("mined") == {"猫"}

    def test_delayed_undo_blocks_close_and_preserves_later_receipt(self, main_window, monkeypatch, test_config, qtbot):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QMessageBox

        from anki_miner.gui import main_window as mw_module
        from anki_miner.gui.controllers.run_receipt import RunReceipt
        from anki_miner.gui.utils.progress_telemetry import ActiveDuration
        from anki_miner.gui.widgets.dialogs import results_dialog as dialog_module
        from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog
        from anki_miner.gui.widgets.inline_receipt import InlineReceipt
        from anki_miner.models import TerminalOutcome

        db_path = test_config.known_words_db_path
        self._setup_known_words_db(db_path, {"猫": "mined"})
        _fake_delete_notes(monkeypatch, deleted_count=1)
        main_window.update_config(replace(main_window.config, use_known_words_db=True))

        pending: dict = {}

        def hold_off_thread(parent, work, on_done, on_error=None, **kwargs):
            pending.update(work=work, on_done=on_done, on_error=on_error)
            return object()

        monkeypatch.setattr(dialog_module, "run_off_thread", hold_off_thread)
        monkeypatch.setattr(
            dialog_module.QMessageBox,
            "question",
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )

        dialogs: list[ResultsDialog] = []

        class _NonBlockingResultsDialog(ResultsDialog):
            def exec(self):
                dialogs.append(self)
                self.show()
                return 0

        monkeypatch.setattr(mw_module, "ResultsDialog", _NonBlockingResultsDialog)

        old_result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[101],
            mined_forms=["猫"],
        )
        old_receipt = RunReceipt(
            outcome=TerminalOutcome.SUCCESS,
            items_total=1,
            items_completed=1,
            items_failed=0,
            notes_added=1,
            note_ids=(101,),
            duration=ActiveDuration(active_s=1.0, suspended_s=0.0),
            results=(old_result,),
        )
        receipt = InlineReceipt()
        qtbot.addWidget(receipt)
        receipt.show_receipt(old_receipt)

        def open_details():
            current = receipt.receipt
            aggregate = current.aggregate_result() if current is not None else None
            if aggregate is not None:
                main_window._on_run_details(aggregate)

        receipt.details_requested.connect(open_details)
        receipt.details_button.click()
        dialog = dialogs[0]
        qtbot.addWidget(dialog)
        qtbot.waitUntil(dialog.isVisible)

        dialog._undo_button.click()
        close_button = dialog.footer_layout.itemAt(dialog.footer_layout.count() - 1).widget()
        assert close_button is not None
        close_button.click()
        assert dialog.isVisible()
        qtbot.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.isVisible()
        dialog.close()
        assert dialog.isVisible()

        deleted = pending["work"]()
        later_result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[202],
            mined_forms=["猫"],
        )
        later_receipt = RunReceipt(
            outcome=TerminalOutcome.SUCCESS,
            items_total=1,
            items_completed=1,
            items_failed=0,
            notes_added=1,
            note_ids=(202,),
            duration=ActiveDuration(active_s=1.0, suspended_s=0.0),
            results=(later_result,),
        )
        receipt.show_receipt(later_receipt)

        pending["on_done"](deleted)

        assert receipt.receipt is later_receipt

    @pytest.mark.parametrize("worker_owner", ["current", "retained"])
    def test_active_mining_task_prevents_undo_overlap(self, main_window, monkeypatch, test_config, qtbot, worker_owner):
        from threading import Event, Thread

        from anki_miner.gui import main_window as mw_module
        from anki_miner.gui.controllers.run_receipt import RunReceipt
        from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
        from anki_miner.gui.utils.progress_telemetry import ActiveDuration
        from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
        from anki_miner.gui.widgets.inline_receipt import InlineReceipt
        from anki_miner.gui.workers.deck_builder_worker import DeckBuilderWorker
        from anki_miner.models import TerminalOutcome
        from anki_miner.models.deck_build import DeckBuildRequest, DeckSelectionMode
        from anki_miner.services.known_word_db import KnownWordDB

        db_path = test_config.known_words_db_path
        self._setup_known_words_db(db_path, {"猫": "mined"})
        main_window.update_config(replace(main_window.config, use_known_words_db=True))

        old_note_deleted = Event()
        release_old_delete = Event()
        notes = {101}

        def blocking_delete_notes(note_ids):
            notes.difference_update(note_ids)
            old_note_deleted.set()
            assert release_old_delete.wait(2)
            return len(note_ids)

        monkeypatch.setattr(main_window._anki_service, "delete_notes", blocking_delete_notes)

        old_result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[101],
            mined_forms=["猫"],
        )
        later_result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[202],
            mined_forms=["猫"],
        )
        later_receipt = RunReceipt(
            outcome=TerminalOutcome.SUCCESS,
            items_total=1,
            items_completed=1,
            items_failed=0,
            notes_added=1,
            note_ids=(202,),
            duration=ActiveDuration(active_s=1.0, suspended_s=0.0),
            results=(later_result,),
        )
        receipt = InlineReceipt()
        qtbot.addWidget(receipt)
        receipt.show_receipt(
            RunReceipt(
                outcome=TerminalOutcome.SUCCESS,
                items_total=1,
                items_completed=1,
                items_failed=0,
                notes_added=1,
                note_ids=(101,),
                duration=ActiveDuration(active_s=1.0, suspended_s=0.0),
                results=(old_result,),
            )
        )

        callbacks: list[object] = []

        class _OverlappingResultsDialog:
            def __init__(self, result, parent, undo_callback=None, on_undo_committed=None):
                callbacks.append(undo_callback)
                self._undo_callback = undo_callback
                self._on_undo_committed = on_undo_committed

            def exec(self):
                deleted: list[int] = []
                undo_thread = None
                if self._undo_callback is not None:

                    def run_undo():
                        deleted.append(self._undo_callback([101]))

                    undo_thread = Thread(target=run_undo)
                    undo_thread.start()
                    assert old_note_deleted.wait(2)

                notes.add(202)
                KnownWordDB(db_path).add_words({"猫"}, source="mined")
                receipt.show_receipt(later_receipt)

                release_old_delete.set()
                if undo_thread is not None:
                    undo_thread.join(2)
                    assert not undo_thread.is_alive()
                    self._on_undo_committed(deleted[0])
                return 0

        monkeypatch.setattr(mw_module, "ResultsDialog", _OverlappingResultsDialog)
        deck_builder_tab = DeckBuilderTab(
            config=main_window.config,
            presenter=GUIPresenter(main_window),
            progress_callback=GUIProgressCallback(main_window),
            parent=main_window.tabs,
        )
        main_window.tabs.addTab(deck_builder_tab, "Deck Builder")
        worker_started = Event()
        release_worker = Event()

        class _LiveDeckBuilderWorker(DeckBuilderWorker):
            def run(self):
                worker_started.set()
                release_worker.wait(2)

        worker = _LiveDeckBuilderWorker(
            request=DeckBuildRequest(
                pairs=[],
                deck_name="Deck Builder",
                mode=DeckSelectionMode.ALL,
                value=0.0,
                collection_filter=False,
            ),
            config=main_window.config,
            presenter=deck_builder_tab.presenter,
            progress_callback=deck_builder_tab.progress_callback,
            parent=deck_builder_tab,
        )
        if worker_owner == "current":
            deck_builder_tab.worker_thread = worker
        else:
            deck_builder_tab.worker_thread = None
            deck_builder_tab._leaked_runs.append((worker, None))
        worker.start()
        assert worker_started.wait(2)
        assert worker.isRunning()
        assert not main_window.task_registry.running()

        def open_details():
            current = receipt.receipt
            aggregate = current.aggregate_result() if current is not None else None
            if aggregate is not None:
                main_window._on_run_details(aggregate)

        try:
            receipt.details_requested.connect(open_details)
            receipt.details_button.click()

            assert KnownWordDB(db_path).get_words_by_source("mined") == {"猫"}
            assert 202 in notes
            assert receipt.receipt is later_receipt
            assert callbacks == [None]
        finally:
            release_worker.set()
            assert worker.wait(2000)

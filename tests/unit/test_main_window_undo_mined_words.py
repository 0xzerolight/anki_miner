"""Tests for OVH-030: Undo reverts source='mined' known-words rows.

When the user clicks Undo after a mining run, the undo_callback in
_on_processing_result must revert the session's source='mined' rows from
known_words.db so those words are re-mineable on the next run.

Issue #42 invariant: source='user' and source='anki' rows are NEVER touched.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import ProcessingResult


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    """Stub out heavy MainWindow startup side effects."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)


@pytest.fixture
def main_window(qtbot, monkeypatch, test_config):
    """MainWindow with heavy startup effects stubbed out."""
    _patch_heavy_init(monkeypatch, test_config)
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

        def __init__(self, result, parent, undo_callback=None):
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
        config_with_kwdb = replace(main_window.config, use_known_words_db=True, enable_history=False)
        main_window.update_config(config_with_kwdb)

        result = ProcessingResult(
            total_words_found=2,
            new_words_found=2,
            cards_created=2,
            card_ids=[10, 11],
            mined_forms=["食べる", "走る"],
        )
        main_window._on_processing_result(result)
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

        config_with_kwdb = replace(main_window.config, use_known_words_db=True, enable_history=False)
        main_window.update_config(config_with_kwdb)

        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[20],
            mined_forms=["食べる"],
        )
        main_window._on_processing_result(result)
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

        config_with_kwdb = replace(main_window.config, use_known_words_db=True, enable_history=False)
        main_window.update_config(config_with_kwdb)

        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[30],
            mined_forms=["走る"],
        )
        main_window._on_processing_result(result)
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

        config_no_kwdb = replace(main_window.config, use_known_words_db=False, enable_history=False)
        main_window.update_config(config_no_kwdb)

        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[40],
            mined_forms=["食べる"],
        )
        main_window._on_processing_result(result)
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

        config = replace(main_window.config, known_words_db_path=absent_db, enable_history=False)
        main_window.update_config(config)

        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[50],
            mined_forms=["食べる"],
        )
        main_window._on_processing_result(result)
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

        config_with_kwdb = replace(main_window.config, use_known_words_db=True, enable_history=False)
        main_window.update_config(config_with_kwdb)

        result = ProcessingResult(
            total_words_found=0,
            new_words_found=0,
            cards_created=0,
            card_ids=[],
            mined_forms=[],  # empty — nothing to revert
        )
        main_window._on_processing_result(result)
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

        config_with_kwdb = replace(main_window.config, use_known_words_db=True, enable_history=False)
        main_window.update_config(config_with_kwdb)

        result = ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[50],
            mined_forms=["食べる"],
        )
        main_window._on_processing_result(result)

        with patch.object(kw_module.KnownWordDB, "remove_words", side_effect=Exception("db blow-up")):
            # Must not raise — undo must succeed even if known_words.db revert fails.
            deleted = captured["cb"]([50])

        assert deleted == 1

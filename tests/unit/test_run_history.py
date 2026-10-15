"""AI-16: show or delete any past run's cards (Review Focus 4 and 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.models.stats import MiningSession
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.run_history import RunNotesCheck, check_run_notes, delete_run
from anki_miner.services.stats_service import StatsService

_POST = "anki_miner.services.anki_service.post_action"


class _FakeAnki:
    """AnkiConnect at the post_action seam: notes 1 and 3 exist, note 3 has been reviewed."""

    def __init__(self, existing=(1, 3), reviewed=(3,)) -> None:
        self.existing = set(existing)
        self.reviewed = set(reviewed)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, *args, **kwargs):
        action = args[1]
        params = kwargs.get("params") or {}
        self.calls.append((action, params))
        if action == "findNotes":
            query = params["query"]
            ids = {int(part) for part in query.split()[0].removeprefix("nid:").split(",")}
            pool = self.reviewed if "-is:new" in query else self.existing
            return sorted(ids & pool)
        if action == "deleteNotes":
            self.existing -= set(params["notes"])
            return None
        raise AssertionError(f"unexpected AnkiConnect action {action}")


def _stats(tmp_path: Path, language: str = "ja") -> StatsService:
    stats = StatsService(tmp_path / "stats.db", language=language)
    assert stats.load()
    return stats


def test_check_skips_notes_deleted_in_anki(test_config, monkeypatch):
    """Review Focus 4: note 2 is gone from Anki, so it is neither counted nor touched."""
    fake = _FakeAnki()
    monkeypatch.setattr(_POST, fake)

    check = check_run_notes(AnkiService(test_config), [3, 2, 1])

    assert check == RunNotesCheck(requested=3, present=(1, 3), reviewed=1)
    assert check.missing == 1


def test_check_of_no_ids_asks_nothing(test_config, monkeypatch):
    fake = _FakeAnki()
    monkeypatch.setattr(_POST, fake)

    assert check_run_notes(AnkiService(test_config), []) == RunNotesCheck(requested=0, present=(), reviewed=0)
    assert fake.calls == []


def test_delete_skips_the_missing_note(tmp_path, test_config, monkeypatch):
    """Review Focus 4: the delete sends only the notes that still exist."""
    stats = _stats(tmp_path)
    session_id = stats.record_session(MiningSession(series_name="S", note_ids=[1, 2, 3]))
    fake = _FakeAnki()
    monkeypatch.setattr(_POST, fake)

    deleted = delete_run(AnkiService(test_config), stats, session_id, known_words_db_for=lambda _code: None)

    assert deleted == 2
    assert ("deleteNotes", {"notes": [1, 3]}) in fake.calls
    receipt = stats.get_run_receipt(session_id)
    assert receipt is not None and receipt.note_ids == ()
    # Ruling C5: all three recorded notes are gone now; the row is marked with that count.
    [row] = stats.get_recent_sessions()
    assert row.deleted_count == 3


def test_delete_reverts_mined_rows_in_the_runs_language(tmp_path, test_config, monkeypatch):
    """Review Focus 5: a ja run deleted while es is active reverts ja's known-words DB only."""
    stats = _stats(tmp_path, "ja")
    ja_id = stats.record_session(MiningSession(series_name="JA", note_ids=[1], mined_forms=["猫"]))
    stats.language = "es"
    dbs = {
        "ja": KnownWordDB(tmp_path / "known_words.db", language="ja"),
        "es": KnownWordDB(tmp_path / "known_words.es.db", language="es"),
    }
    for db in dbs.values():
        db.initialize()
    dbs["ja"].add_words({"猫"}, source="mined")
    dbs["ja"].add_words({"犬"}, source="user")
    dbs["es"].add_words({"猫"}, source="mined")
    monkeypatch.setattr(_POST, _FakeAnki(existing=(1,), reviewed=()))

    delete_run(AnkiService(test_config), stats, ja_id, known_words_db_for=dbs.get)

    assert dbs["ja"].get_words_by_source("mined") == set()
    assert dbs["ja"].get_words_by_source("user") == {"犬"}
    assert dbs["es"].get_words_by_source("mined") == {"猫"}


def test_an_unreachable_anki_deletes_nothing_and_keeps_the_receipt(tmp_path, test_config, monkeypatch):
    """Review Focus 4: Anki down is an error for the caller; the run keeps its list for a retry."""
    stats = _stats(tmp_path)
    session_id = stats.record_session(MiningSession(series_name="S", note_ids=[1], mined_forms=["猫"]))

    def _down(*_args, **_kwargs):
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?")

    monkeypatch.setattr(_POST, _down)

    with pytest.raises(AnkiConnectionError):
        delete_run(AnkiService(test_config), stats, session_id, known_words_db_for=lambda _code: None)

    receipt = stats.get_run_receipt(session_id)
    assert receipt is not None and receipt.note_ids == (1,)


def test_an_unknown_session_deletes_nothing(tmp_path, test_config, monkeypatch):
    fake = _FakeAnki()
    monkeypatch.setattr(_POST, fake)

    assert delete_run(AnkiService(test_config), _stats(tmp_path), 999, known_words_db_for=lambda _code: None) == 0
    assert fake.calls == []


def test_long_runs_are_asked_in_chunks(test_config, monkeypatch):
    fake = _FakeAnki(existing=range(1, 1001), reviewed=())
    monkeypatch.setattr(_POST, fake)

    check = check_run_notes(AnkiService(test_config), list(range(1, 1001)))

    assert len(check.present) == 1000
    find_queries = [params["query"] for action, params in fake.calls if action == "findNotes"]
    assert all(len(query.split()[0].split(",")) <= 400 for query in find_queries)

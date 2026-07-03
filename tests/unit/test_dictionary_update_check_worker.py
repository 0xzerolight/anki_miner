"""Unit tests for the dictionary update-check worker.

Network is fully mocked via an injected fake session; ``run()`` is invoked
directly (no QThread start) so no real thread or socket is created.
"""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui.workers.dictionary_update_check_worker import (
    DictionaryUpdateCheckWorker,
    UpdateCheckOutcome,
)
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip


class _FakeResponse:
    def __init__(self, payload: object):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


class _FakeSession:
    def __init__(self, payload: object):
        self._payload = payload
        self.closed = False

    def get(self, url: str, timeout: object = None) -> _FakeResponse:
        return _FakeResponse(self._payload)

    def close(self) -> None:
        self.closed = True


def _import_updatable(tmp_path: Path, *, revision: str = "1.0") -> tuple[str, Path]:
    zip_path = build_yomitan_zip(
        tmp_path / "src" / f"{revision}.zip",
        revision=revision,
        index_extra={
            "isUpdatable": True,
            "indexUrl": "https://example.com/index.json",
            "downloadUrl": "https://example.com/dict.zip",
        },
    )
    result = import_yomitan_zip(zip_path, tmp_path / "dicts")
    return result.dict_id, tmp_path / "dicts" / result.dict_id / "index.sqlite"


def test_reports_available_update(tmp_path: Path) -> None:
    dict_id, db_path = _import_updatable(tmp_path, revision="1.0")
    session = _FakeSession({"title": "D", "revision": "2.0", "format": 3})
    worker = DictionaryUpdateCheckWorker(
        [(dict_id, "My Dict", db_path)],
        session_factory=lambda: session,
    )
    captured: list = []
    worker.check_finished.connect(captured.append)
    worker.run()

    assert len(captured) == 1
    outcomes = captured[0]
    assert len(outcomes) == 1
    outcome: UpdateCheckOutcome = outcomes[0]
    assert outcome.display_name == "My Dict"
    assert outcome.info is not None
    assert outcome.info.latest_revision == "2.0"
    assert outcome.error is None
    assert session.closed is True


def test_up_to_date_produces_no_outcome(tmp_path: Path) -> None:
    dict_id, db_path = _import_updatable(tmp_path, revision="2.0")
    session = _FakeSession({"title": "D", "revision": "2.0", "format": 3})
    worker = DictionaryUpdateCheckWorker(
        [(dict_id, "My Dict", db_path)],
        session_factory=lambda: session,
    )
    captured: list = []
    worker.check_finished.connect(captured.append)
    worker.run()

    assert captured == [[]]


def test_invalid_remote_index_becomes_error_outcome(tmp_path: Path) -> None:
    dict_id, db_path = _import_updatable(tmp_path)
    session = _FakeSession({"revision": "2.0"})  # missing title → invalid
    worker = DictionaryUpdateCheckWorker(
        [(dict_id, "My Dict", db_path)],
        session_factory=lambda: session,
    )
    captured: list = []
    worker.check_finished.connect(captured.append)
    worker.run()

    outcomes = captured[0]
    assert len(outcomes) == 1
    assert outcomes[0].info is None
    assert outcomes[0].error is not None


def test_cancellation_stops_before_first_job(tmp_path: Path) -> None:
    dict_id, db_path = _import_updatable(tmp_path)
    session = _FakeSession({"title": "D", "revision": "9.0", "format": 3})
    worker = DictionaryUpdateCheckWorker(
        [(dict_id, "My Dict", db_path)],
        session_factory=lambda: session,
    )
    worker.cancel()
    captured: list = []
    worker.check_finished.connect(captured.append)
    worker.run()

    # Cancelled before any check ran → empty outcome list, session still closed.
    assert captured == [[]]
    assert session.closed is True

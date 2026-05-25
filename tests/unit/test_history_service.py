"""Tests for history_service module."""

import json
import sqlite3

import pytest

from anki_miner.models.processing import ProcessingResult
from anki_miner.services.history_service import HistoryService


@pytest.fixture
def history_service(tmp_path):
    """Create a HistoryService with a temporary database."""
    db_path = tmp_path / "test_history.db"
    service = HistoryService(db_path)
    service.initialize()
    return service


@pytest.fixture
def sample_result():
    """Create a sample ProcessingResult."""
    return ProcessingResult(
        total_words_found=50,
        new_words_found=10,
        cards_created=8,
        errors=[],
        elapsed_time=12.5,
    )


def _fetch_row(db_path, row_id):
    """Helper: fetch a single row as a dict."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM mining_history WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# TestInitialize
# ---------------------------------------------------------------------------


class TestInitialize:
    """Tests for HistoryService.initialize."""

    def test_creates_database_file(self, tmp_path):
        """Should create the database file and table."""
        db_path = tmp_path / "subdir" / "history.db"
        service = HistoryService(db_path)
        service.initialize()
        assert db_path.exists()

    def test_initialize_is_idempotent(self, tmp_path):
        """Should not fail if called multiple times."""
        db_path = tmp_path / "history.db"
        service = HistoryService(db_path)
        service.initialize()
        service.initialize()  # Should not raise
        assert db_path.exists()

    def test_initialize_creates_indexes(self, tmp_path):
        """Should create indexes on timestamp and series_name."""
        db_path = tmp_path / "history.db"
        service = HistoryService(db_path)
        service.initialize()
        with sqlite3.connect(str(db_path)) as conn:
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(mining_history)")}
        assert "idx_history_timestamp" in indexes
        assert "idx_history_series" in indexes


# ---------------------------------------------------------------------------
# TestRecordSession
# ---------------------------------------------------------------------------


class TestRecordSession:
    """Tests for HistoryService.record_session."""

    def test_record_returns_row_id(self, history_service, sample_result, tmp_path):
        """Should return the row ID of the inserted record."""
        video = tmp_path / "anime" / "ep01.mkv"
        subtitle = tmp_path / "anime" / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        assert isinstance(row_id, int)
        assert row_id >= 1

    def test_record_stores_card_ids(self, history_service, sample_result, tmp_path):
        """Should store card IDs as a JSON array."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        card_ids = [100, 200, 300]
        row_id = history_service.record_session(video, subtitle, sample_result, card_ids=card_ids)
        row = _fetch_row(history_service.db_path, row_id)
        assert row is not None
        assert json.loads(row["card_ids"]) == [100, 200, 300]

    def test_record_with_no_card_ids(self, history_service, sample_result, tmp_path):
        """Should default to empty list when no card IDs provided."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        row = _fetch_row(history_service.db_path, row_id)
        assert row is not None
        assert json.loads(row["card_ids"]) == []

    def test_record_stores_result_fields(self, history_service, sample_result, tmp_path):
        """Should store cards_created and elapsed_time from result."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        row = _fetch_row(history_service.db_path, row_id)
        assert row is not None
        assert row["cards_created"] == 8
        assert row["elapsed_time"] == pytest.approx(12.5)

    def test_record_stores_file_paths(self, history_service, sample_result, tmp_path):
        """Should store video and subtitle file paths as strings."""
        video = tmp_path / "anime" / "ep01.mkv"
        subtitle = tmp_path / "subs" / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        row = _fetch_row(history_service.db_path, row_id)
        assert row is not None
        assert str(video) in row["video_file"]
        assert str(subtitle) in row["subtitle_file"]

    def test_series_name_from_parent_dir(self, history_service, sample_result, tmp_path):
        """Should extract series name from video's parent directory."""
        video = tmp_path / "My Anime" / "ep01.mkv"
        subtitle = tmp_path / "My Anime" / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        row = _fetch_row(history_service.db_path, row_id)
        assert row is not None
        assert row["series_name"] == "My Anime"

    def test_multiple_records_get_unique_ids(self, history_service, sample_result, tmp_path):
        """Should assign unique IDs to each record."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        id1 = history_service.record_session(video, subtitle, sample_result)
        id2 = history_service.record_session(video, subtitle, sample_result)
        id3 = history_service.record_session(video, subtitle, sample_result)
        assert len({id1, id2, id3}) == 3

    def test_record_stores_words_mined(self, history_service, sample_result, tmp_path):
        """Should serialize the words_mined list as JSON."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result, words_mined=["猫", "犬"])
        row = _fetch_row(history_service.db_path, row_id)
        assert row is not None
        assert json.loads(row["words_mined"]) == ["猫", "犬"]

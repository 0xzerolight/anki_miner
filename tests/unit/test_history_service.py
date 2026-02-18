"""Tests for history_service module."""

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
        retrieved_ids = history_service.get_card_ids_for_session(row_id)
        assert retrieved_ids == [100, 200, 300]

    def test_record_with_no_card_ids(self, history_service, sample_result, tmp_path):
        """Should default to empty list when no card IDs provided."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        retrieved_ids = history_service.get_card_ids_for_session(row_id)
        assert retrieved_ids == []

    def test_record_stores_result_fields(self, history_service, sample_result, tmp_path):
        """Should store cards_created and elapsed_time from result."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        session = history_service.get_session(row_id)
        assert session is not None
        assert session["cards_created"] == 8
        assert session["elapsed_time"] == pytest.approx(12.5)

    def test_record_stores_file_paths(self, history_service, sample_result, tmp_path):
        """Should store video and subtitle file paths as strings."""
        video = tmp_path / "anime" / "ep01.mkv"
        subtitle = tmp_path / "subs" / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        session = history_service.get_session(row_id)
        assert session is not None
        assert str(video) in session["video_file"]
        assert str(subtitle) in session["subtitle_file"]

    def test_series_name_from_parent_dir(self, history_service, sample_result, tmp_path):
        """Should extract series name from video's parent directory."""
        video = tmp_path / "My Anime" / "ep01.mkv"
        subtitle = tmp_path / "My Anime" / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        session = history_service.get_session(row_id)
        assert session is not None
        assert session["series_name"] == "My Anime"

    def test_multiple_records_get_unique_ids(self, history_service, sample_result, tmp_path):
        """Should assign unique IDs to each record."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        id1 = history_service.record_session(video, subtitle, sample_result)
        id2 = history_service.record_session(video, subtitle, sample_result)
        id3 = history_service.record_session(video, subtitle, sample_result)
        assert len({id1, id2, id3}) == 3


# ---------------------------------------------------------------------------
# TestGetHistory
# ---------------------------------------------------------------------------


class TestGetHistory:
    """Tests for HistoryService.get_history."""

    def test_empty_history(self, history_service):
        """Should return empty list when no sessions recorded."""
        result = history_service.get_history()
        assert result == []

    def test_returns_newest_first(self, history_service, sample_result, tmp_path):
        """Should return entries ordered newest first."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        id1 = history_service.record_session(video, subtitle, sample_result)
        id2 = history_service.record_session(video, subtitle, sample_result)
        id3 = history_service.record_session(video, subtitle, sample_result)

        history = history_service.get_history()
        assert len(history) == 3
        assert history[0]["id"] == id3
        assert history[1]["id"] == id2
        assert history[2]["id"] == id1

    def test_respects_limit(self, history_service, sample_result, tmp_path):
        """Should respect the limit parameter."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        for _ in range(10):
            history_service.record_session(video, subtitle, sample_result)

        history = history_service.get_history(limit=3)
        assert len(history) == 3

    def test_parses_json_fields(self, history_service, sample_result, tmp_path):
        """Should parse card_ids and words_mined as lists."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        history_service.record_session(video, subtitle, sample_result, card_ids=[100, 200])

        history = history_service.get_history()
        assert len(history) == 1
        assert history[0]["card_ids"] == [100, 200]
        assert isinstance(history[0]["words_mined"], list)


# ---------------------------------------------------------------------------
# TestGetSession
# ---------------------------------------------------------------------------


class TestGetSession:
    """Tests for HistoryService.get_session."""

    def test_existing_session(self, history_service, sample_result, tmp_path):
        """Should return the correct session dict."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        session = history_service.get_session(row_id)
        assert session is not None
        assert session["id"] == row_id

    def test_nonexistent_session(self, history_service):
        """Should return None for a non-existent session."""
        result = history_service.get_session(9999)
        assert result is None


# ---------------------------------------------------------------------------
# TestGetCardIdsForSession
# ---------------------------------------------------------------------------


class TestGetCardIdsForSession:
    """Tests for HistoryService.get_card_ids_for_session."""

    def test_returns_card_ids(self, history_service, sample_result, tmp_path):
        """Should return stored card IDs."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        row_id = history_service.record_session(
            video, subtitle, sample_result, card_ids=[10, 20, 30]
        )
        ids = history_service.get_card_ids_for_session(row_id)
        assert ids == [10, 20, 30]

    def test_nonexistent_session_returns_empty(self, history_service):
        """Should return empty list for non-existent session."""
        ids = history_service.get_card_ids_for_session(9999)
        assert ids == []


# ---------------------------------------------------------------------------
# TestDeleteSession
# ---------------------------------------------------------------------------


class TestDeleteSession:
    """Tests for HistoryService.delete_session."""

    def test_delete_existing(self, history_service, sample_result, tmp_path):
        """Should delete the session and return True."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        row_id = history_service.record_session(video, subtitle, sample_result)
        assert history_service.delete_session(row_id) is True
        assert history_service.get_session(row_id) is None

    def test_delete_nonexistent(self, history_service):
        """Should return False when session doesn't exist."""
        assert history_service.delete_session(9999) is False

    def test_delete_does_not_affect_others(self, history_service, sample_result, tmp_path):
        """Should only delete the specified session."""
        video = tmp_path / "ep01.mkv"
        subtitle = tmp_path / "ep01.ass"
        id1 = history_service.record_session(video, subtitle, sample_result)
        id2 = history_service.record_session(video, subtitle, sample_result)
        history_service.delete_session(id1)
        assert history_service.get_session(id2) is not None

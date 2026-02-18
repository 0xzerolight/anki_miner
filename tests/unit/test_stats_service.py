"""Tests for StatsService."""

import pytest

from anki_miner.models.stats import MiningSession
from anki_miner.services.stats_service import StatsService


class TestInitialization:
    """Tests for StatsService initialization."""

    def test_load_creates_database(self, tmp_path):
        db_path = tmp_path / "stats.db"
        service = StatsService(db_path)
        assert service.load() is True
        assert db_path.exists()
        assert service.is_available() is True

    def test_load_creates_parent_directories(self, tmp_path):
        db_path = tmp_path / "subdir" / "stats.db"
        service = StatsService(db_path)
        assert service.load() is True
        assert db_path.exists()

    def test_is_available_false_before_load(self, tmp_path):
        service = StatsService(tmp_path / "stats.db")
        assert service.is_available() is False

    def test_load_idempotent(self, tmp_path):
        service = StatsService(tmp_path / "stats.db")
        assert service.load() is True
        assert service.load() is True


class TestRecordSession:
    """Tests for recording mining sessions."""

    @pytest.fixture
    def service(self, tmp_path):
        svc = StatsService(tmp_path / "stats.db")
        svc.load()
        return svc

    def test_record_and_retrieve(self, service):
        session = MiningSession(
            series_name="Spy x Family",
            episode_name="episode_01",
            total_words=500,
            unknown_words=50,
            cards_created=30,
            elapsed_time=12.5,
        )
        row_id = service.record_session(session)
        assert row_id > 0

        sessions = service.get_recent_sessions(limit=1)
        assert len(sessions) == 1
        assert sessions[0].series_name == "Spy x Family"
        assert sessions[0].cards_created == 30

    def test_recent_sessions_ordered_by_date(self, service):
        for i in range(5):
            service.record_session(
                MiningSession(
                    series_name="Test",
                    episode_name=f"ep_{i:02d}",
                    total_words=100,
                    unknown_words=10,
                    cards_created=i,
                    elapsed_time=1.0,
                )
            )
        sessions = service.get_recent_sessions(limit=5)
        # Most recent (cards_created=4) should be first
        assert sessions[0].cards_created == 4

    def test_recent_sessions_respects_limit(self, service):
        for i in range(10):
            service.record_session(
                MiningSession(
                    series_name="Test",
                    episode_name=f"ep_{i:02d}",
                    total_words=100,
                    unknown_words=10,
                    cards_created=i,
                    elapsed_time=1.0,
                )
            )
        sessions = service.get_recent_sessions(limit=3)
        assert len(sessions) == 3

    def test_record_returns_negative_when_not_initialized(self, tmp_path):
        service = StatsService(tmp_path / "stats.db")
        result = service.record_session(
            MiningSession(
                series_name="Test",
                episode_name="ep01",
            )
        )
        assert result == -1


class TestOverallStats:
    """Tests for overall statistics."""

    @pytest.fixture
    def service(self, tmp_path):
        svc = StatsService(tmp_path / "stats.db")
        svc.load()
        return svc

    def test_empty_database(self, service):
        stats = service.get_overall_stats()
        assert stats.total_sessions == 0
        assert stats.total_cards_created == 0
        assert stats.series_count == 0
        assert stats.avg_cards_per_session == 0.0

    def test_aggregates_correctly(self, service):
        service.record_session(
            MiningSession(
                series_name="Series A",
                episode_name="ep01",
                total_words=500,
                unknown_words=50,
                cards_created=30,
                elapsed_time=10.0,
            )
        )
        service.record_session(
            MiningSession(
                series_name="Series B",
                episode_name="ep01",
                total_words=300,
                unknown_words=40,
                cards_created=20,
                elapsed_time=8.0,
            )
        )

        stats = service.get_overall_stats()
        assert stats.total_sessions == 2
        assert stats.total_cards_created == 50
        assert stats.total_words_encountered == 800
        assert stats.total_unknown_words == 90
        assert stats.series_count == 2
        assert stats.avg_cards_per_session == 25.0


class TestSeriesStats:
    """Tests for per-series statistics."""

    @pytest.fixture
    def service(self, tmp_path):
        svc = StatsService(tmp_path / "stats.db")
        svc.load()
        return svc

    def test_groups_by_series(self, service):
        for i in range(3):
            service.record_session(
                MiningSession(
                    series_name="Spy x Family",
                    episode_name=f"ep_{i:02d}",
                    total_words=500,
                    unknown_words=50,
                    cards_created=30,
                    elapsed_time=10.0,
                )
            )
        service.record_session(
            MiningSession(
                series_name="Jujutsu Kaisen",
                episode_name="ep_01",
                total_words=600,
                unknown_words=80,
                cards_created=40,
                elapsed_time=12.0,
            )
        )

        series_list = service.get_series_stats()
        assert len(series_list) == 2
        spy = next(s for s in series_list if s.series_name == "Spy x Family")
        assert spy.episodes_mined == 3
        assert spy.total_cards_created == 90

    def test_empty_database(self, service):
        assert service.get_series_stats() == []


class TestDifficulty:
    """Tests for difficulty recording and ranking."""

    @pytest.fixture
    def service(self, tmp_path):
        svc = StatsService(tmp_path / "stats.db")
        svc.load()
        return svc

    def test_record_and_retrieve_difficulty(self, service):
        service.record_difficulty("Easy Show", "ep01", 500, 50, 400)
        service.record_difficulty("Hard Show", "ep01", 500, 250, 400)

        rankings = service.get_series_difficulty()
        assert len(rankings) == 2
        # Easy show should come first (lower difficulty score)
        assert rankings[0].series_name == "Easy Show"
        assert rankings[0].difficulty_score < rankings[1].difficulty_score

    def test_difficulty_score_calculation(self, service):
        service.record_difficulty("Test", "ep01", 100, 25, 80)
        rankings = service.get_series_difficulty()
        assert abs(rankings[0].difficulty_score - 0.25) < 0.01

    def test_skips_zero_total_words(self, service):
        service.record_difficulty("Test", "ep01", 0, 0, 0)
        rankings = service.get_series_difficulty()
        assert len(rankings) == 0

    def test_averages_across_episodes(self, service):
        service.record_difficulty("Show", "ep01", 100, 10, 80)  # 0.10
        service.record_difficulty("Show", "ep02", 100, 30, 80)  # 0.30
        rankings = service.get_series_difficulty()
        assert len(rankings) == 1
        assert abs(rankings[0].difficulty_score - 0.20) < 0.01

    def test_empty_when_not_initialized(self, tmp_path):
        service = StatsService(tmp_path / "stats.db")
        assert service.get_series_difficulty() == []


class TestMilestones:
    """Tests for milestone calculations."""

    @pytest.fixture
    def service(self, tmp_path):
        svc = StatsService(tmp_path / "stats.db")
        svc.load()
        return svc

    def test_no_milestones_achieved_initially(self, service):
        milestones = service.get_milestones()
        assert len(milestones) > 0
        assert all(not m.achieved for m in milestones)

    def test_card_milestone_achieved(self, service):
        # Create enough sessions to get 60 cards (50+ threshold)
        for i in range(5):
            service.record_session(
                MiningSession(
                    series_name="Test",
                    episode_name=f"ep_{i:02d}",
                    total_words=100,
                    unknown_words=20,
                    cards_created=12,
                    elapsed_time=5.0,
                )
            )
        milestones = service.get_milestones()
        first_step = next(m for m in milestones if m.name == "First Steps")
        assert first_step.achieved is True
        assert first_step.current_value == 60

    def test_session_milestone_achieved(self, service):
        for i in range(5):
            service.record_session(
                MiningSession(
                    series_name="Test",
                    episode_name=f"ep_{i:02d}",
                    total_words=100,
                    unknown_words=20,
                    cards_created=5,
                    elapsed_time=5.0,
                )
            )
        milestones = service.get_milestones()
        regular = next(m for m in milestones if m.name == "Regular Miner")
        assert regular.achieved is True

    def test_milestones_returns_empty_when_not_initialized(self, tmp_path):
        service = StatsService(tmp_path / "stats.db")
        assert service.get_milestones() == []


class TestSeriesProgress:
    """Tests for series progress tracking."""

    @pytest.fixture
    def service(self, tmp_path):
        svc = StatsService(tmp_path / "stats.db")
        svc.load()
        return svc

    def test_returns_chronological_sessions(self, service):
        for i in range(3):
            service.record_session(
                MiningSession(
                    series_name="Spy x Family",
                    episode_name=f"ep_{i:02d}",
                    total_words=500,
                    unknown_words=50 - i * 5,
                    cards_created=30,
                    elapsed_time=10.0,
                )
            )

        progress = service.get_series_progress("Spy x Family")
        assert len(progress) == 3
        assert progress[0].episode_name == "ep_00"
        assert progress[2].episode_name == "ep_02"

    def test_filters_by_series_name(self, service):
        service.record_session(
            MiningSession(
                series_name="Show A",
                episode_name="ep01",
                total_words=100,
                unknown_words=10,
                cards_created=5,
            )
        )
        service.record_session(
            MiningSession(
                series_name="Show B",
                episode_name="ep01",
                total_words=200,
                unknown_words=20,
                cards_created=10,
            )
        )

        progress = service.get_series_progress("Show A")
        assert len(progress) == 1
        assert progress[0].series_name == "Show A"

    def test_returns_empty_for_unknown_series(self, service):
        assert service.get_series_progress("Nonexistent") == []

    def test_returns_empty_when_not_initialized(self, tmp_path):
        service = StatsService(tmp_path / "stats.db")
        assert service.get_series_progress("Test") == []

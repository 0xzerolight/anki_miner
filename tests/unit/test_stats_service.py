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

    def test_load_exception_returns_false(self, tmp_path):
        """Database initialization failure should return False."""
        from unittest.mock import patch

        service = StatsService(tmp_path / "stats.db")
        with patch.object(service, "_connect", side_effect=RuntimeError("db error")):
            assert service.load() is False
        assert service.is_available() is False

    def test_methods_return_empty_when_not_initialized(self, tmp_path):
        """All query methods should return safe defaults when not initialized."""
        from anki_miner.models.stats import OverallStats

        service = StatsService(tmp_path / "stats.db")
        # Don't call load()

        overall = service.get_overall_stats()
        assert isinstance(overall, OverallStats)
        assert overall.total_sessions == 0

        assert service.get_recent_sessions() == []


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
    """Tests for milestone calculations (one per category)."""

    @pytest.fixture
    def service(self, tmp_path):
        svc = StatsService(tmp_path / "stats.db")
        svc.load()
        return svc

    def test_returns_one_per_category(self, service):
        """Should return exactly 3 milestones (cards, sessions, series)."""
        milestones = service.get_milestones()
        assert len(milestones) == 3

    def test_shows_first_unachieved_initially(self, service):
        """At zero progress, each milestone should be the lowest threshold."""
        milestones = service.get_milestones()
        assert all(not m.achieved for m in milestones)
        # First card milestone is 50
        assert milestones[0].threshold == 50
        assert milestones[0].name == "First Steps"
        # First session milestone is 5
        assert milestones[1].threshold == 5
        # First series milestone is 3
        assert milestones[2].threshold == 3

    def test_advances_to_next_milestone(self, service):
        """After achieving a milestone, should show the next one in that category."""
        # Create 60 cards across 5 sessions from 1 series
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
        # Cards: 60 achieved, so 50-card milestone passed → shows 100-card milestone
        card_milestone = milestones[0]
        assert card_milestone.threshold == 100
        assert card_milestone.current_value == 60
        assert card_milestone.achieved is False
        # Sessions: 5 achieved → shows 10-session milestone
        session_milestone = milestones[1]
        assert session_milestone.threshold == 10
        assert session_milestone.achieved is False

    def test_shows_last_when_all_achieved(self, service):
        """When all milestones in a category are achieved, show the last one."""
        from anki_miner.models.stats import OverallStats

        stats = OverallStats(
            total_sessions=999,
            total_cards_created=99999,
            series_count=999,
        )
        milestones = service.get_milestones(stats=stats)
        assert len(milestones) == 3
        assert all(m.achieved for m in milestones)
        # Last card milestone is 10000
        assert milestones[0].threshold == 10000
        # Last session milestone is 100
        assert milestones[1].threshold == 100
        # Last series milestone is 25
        assert milestones[2].threshold == 25

    def test_milestones_returns_empty_when_not_initialized(self, tmp_path):
        service = StatsService(tmp_path / "stats.db")
        assert service.get_milestones() == []

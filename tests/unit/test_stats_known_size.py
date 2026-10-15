"""ST-05: one known-words count per day and mining language, kept in stats.db."""

from __future__ import annotations

from datetime import date

from anki_miner.models.stats import KnownSizePoint, MiningSession
from anki_miner.services.stats_service import StatsService


def _service(tmp_path, language: str = "ja") -> StatsService:
    service = StatsService(tmp_path / "stats.db", language=language)
    assert service.load()
    return service


def test_one_row_per_day_and_the_last_count_of_a_day_wins(tmp_path):
    service = _service(tmp_path)

    service.record_known_size(100, day=date(2026, 10, 14))
    service.record_known_size(120, day=date(2026, 10, 15))
    service.record_known_size(125, day=date(2026, 10, 15))

    assert service.get_known_size_history() == [
        KnownSizePoint(day=date(2026, 10, 14), word_count=100),
        KnownSizePoint(day=date(2026, 10, 15), word_count=125),
    ]


def test_languages_keep_separate_histories(tmp_path):
    """Review Focus 5: a language switch reads and writes that language's points only."""
    service = _service(tmp_path, "ja")
    service.record_known_size(100, day=date(2026, 10, 15))

    service.language = "es"
    assert service.get_known_size_history() == []
    service.record_known_size(7, day=date(2026, 10, 15))
    assert service.get_known_size_history() == [KnownSizePoint(day=date(2026, 10, 15), word_count=7)]

    service.language = "ja"
    assert service.get_known_size_history() == [KnownSizePoint(day=date(2026, 10, 15), word_count=100)]


def test_the_default_day_is_today(tmp_path):
    service = _service(tmp_path)

    service.record_known_size(3)

    [point] = service.get_known_size_history()
    assert point.day == date.today()


def test_reset_keeps_the_known_words_history(tmp_path):
    """Reset Statistics deletes sessions and difficulty rows; the known count is not a session."""
    service = _service(tmp_path)
    service.record_session(MiningSession(series_name="S"))
    service.record_known_size(50, day=date(2026, 10, 15))

    service.reset()

    assert service.get_known_size_history() == [KnownSizePoint(day=date(2026, 10, 15), word_count=50)]


def test_an_unloaded_service_has_no_history(tmp_path):
    assert StatsService(tmp_path / "stats.db").get_known_size_history() == []

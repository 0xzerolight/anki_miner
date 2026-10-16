"""ST-05 and ST-07 on Analytics: the known-words count and frequency coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from anki_miner.config import FreqEntry
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.widgets import analytics_tab as analytics_tab_module
from anki_miner.models.stats import KnownSizePoint
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.frequency import storage
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.stats_service import StatsService


def _source(freqs: Path, source_id: str, language: str, rows: list[tuple[str, int]]) -> None:
    meta = {
        "schema_version": str(storage.SCHEMA_VERSION),
        "format": "csv",
        "source_name": source_id,
        "entry_count": str(len(rows)),
        "language": language,
    }
    storage.build_index(freqs / source_id / "index.sqlite", [(term, None, rank, None) for term, rank in rows], meta)


def _stats(tmp_path: Path, language: str = "ja") -> StatsService:
    stats = StatsService(tmp_path / "stats.db", language=language)
    assert stats.load()
    return stats


def test_read_known_forms_uses_the_active_languages_db(tmp_path, test_config, monkeypatch):
    """Review Focus 5: an es config reads Anki plus es's own list, never ja's."""
    config = replace(test_config, language="es", known_words_db_path=tmp_path / "known_words.db")
    ja_db = KnownWordDB(tmp_path / "known_words.db", language="ja")
    ja_db.initialize()
    ja_db.add_words({"猫"}, source="user")
    es_db = KnownWordDB(tmp_path / "known_words.es.db", language="es")
    es_db.initialize()
    es_db.add_words({"gato"}, source="user")
    monkeypatch.setattr(AnkiService, "get_existing_vocabulary", lambda self, *, allow_degraded=True: {"perro"})

    known, fold = analytics_tab_module._read_known_forms(config)

    assert known == {"perro", "gato"}
    assert fold("Gato") == "gato"


def test_fetch_records_today_and_measures_coverage(tmp_path, test_config, monkeypatch):
    _source(tmp_path / "freqs", "list", "ja", [("猫", 1), ("犬", 2)])
    config = replace(test_config, freqs_root=tmp_path / "freqs", frequency_chain=(FreqEntry(source_id="list"),))
    stats = _stats(tmp_path)
    monkeypatch.setattr(analytics_tab_module, "_read_known_forms", lambda _config: ({"猫"}, str))

    bundle = analytics_tab_module._fetch_knowledge(config, stats)

    assert bundle.known_count == 1
    assert bundle.history == [KnownSizePoint(day=date.today(), word_count=1)]
    assert bundle.has_numeric_source is True
    assert bundle.coverage is not None
    assert (bundle.coverage.bands[0].known, bundle.coverage.bands[0].missing) == (1, ((2, "犬"),))


def test_anki_unreachable_keeps_the_last_count(tmp_path, test_config, monkeypatch):
    stats = _stats(tmp_path)
    stats.record_known_size(40, day=date(2026, 10, 1))

    def _down(_config):
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?")

    monkeypatch.setattr(analytics_tab_module, "_read_known_forms", _down)

    bundle = analytics_tab_module._fetch_knowledge(test_config, stats)

    assert bundle.known_count is None
    assert "Cannot connect" in bundle.anki_error
    assert [point.word_count for point in bundle.history] == [40]
    assert bundle.coverage is None


def test_no_numeric_source_skips_coverage(tmp_path, test_config, monkeypatch):
    monkeypatch.setattr(analytics_tab_module, "_read_known_forms", lambda _config: ({"猫", "犬"}, str))

    bundle = analytics_tab_module._fetch_knowledge(replace(test_config, frequency_chain=()), _stats(tmp_path))

    assert (bundle.known_count, bundle.has_numeric_source, bundle.coverage) == (2, False, None)


def test_a_language_mismatch_records_nothing(tmp_path, test_config, monkeypatch):
    """Review Focus 5: a ja count is never stored while the stats service is on es."""
    stats = _stats(tmp_path, "es")
    monkeypatch.setattr(analytics_tab_module, "_read_known_forms", lambda _config: ({"猫"}, str))

    analytics_tab_module._fetch_knowledge(test_config, stats)

    assert stats.get_known_size_history() == []
    stats.language = "ja"
    assert stats.get_known_size_history() == []


@pytest.mark.parametrize(
    ("days_ago", "expected_index"),
    [
        ((40, 20, 10, 0), 1),  # earliest point inside the 30-day window
        ((90, 60, 0), 1),  # none inside the window: the latest older one
        ((0,), None),  # a single point has nothing to compare with
    ],
)
def test_growth_reference(days_ago, expected_index):
    today = date(2026, 10, 15)
    history = [KnownSizePoint(day=today - timedelta(days=days), word_count=100 + i) for i, days in enumerate(days_ago)]

    reference = analytics_tab_module._growth_reference(history, today=today)

    assert reference == (None if expected_index is None else history[expected_index])

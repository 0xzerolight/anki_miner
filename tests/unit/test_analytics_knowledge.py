"""ST-05 and ST-07 on Analytics: the known-words count and frequency coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.config import FreqEntry
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.widgets import analytics_tab as analytics_tab_module
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab, _KnowledgeBundle
from anki_miner.languages.registry import get_profile
from anki_miner.models.stats import KnownSizePoint, MiningSession, OverallStats
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.frequency import storage
from anki_miner.services.frequency.coverage import CoverageBand, FrequencyCoverage
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


def _sync_run_off_thread(parent, work, on_done, on_error=None, *, error_prefix=""):
    try:
        result = work()
    except Exception as exc:  # noqa: BLE001 — mirror the worker error path
        if on_error is not None:
            on_error(f"{error_prefix}{exc}")
        return None
    on_done(result)
    return None


TODAY = date.today()


def _bundle(**overrides) -> _KnowledgeBundle:
    values: dict = {
        "language": "ja",
        "known_count": 1250,
        "history": [
            KnownSizePoint(day=TODAY - timedelta(days=10), word_count=1200),
            KnownSizePoint(day=TODAY, word_count=1250),
        ],
        "coverage": None,
        "has_numeric_source": False,
    }
    values.update(overrides)
    return _KnowledgeBundle(**values)


@pytest.fixture
def knowledge(qtbot, monkeypatch, test_config):
    """A tab with one run on record, a fake knowledge fetch, and the fetch's call log."""
    monkeypatch.setattr(analytics_tab_module, "run_off_thread", _sync_run_off_thread)
    state: dict = {"bundle": _bundle(), "calls": [], "error": None}

    def _fake_fetch(config, service):
        state["calls"].append(config.language)
        if state["error"] is not None:
            raise state["error"]
        return state["bundle"]

    monkeypatch.setattr(analytics_tab_module, "_fetch_knowledge", _fake_fetch)

    def _make() -> AnalyticsTab:
        service = MagicMock()
        service.is_available.return_value = True
        service.language = "ja"
        service.get_overall_stats.return_value = OverallStats(total_sessions=1, total_cards_created=3, series_count=1)
        service.get_recent_sessions.return_value = [MiningSession(id=1, series_name="S", cards_created=3)]
        service.get_series_difficulty.return_value = []
        service.get_milestones.return_value = []
        service.get_known_size_history.return_value = []
        tab = AnalyticsTab(service, config_provider=lambda: test_config)
        qtbot.addWidget(tab)
        return tab

    state["make"] = _make
    return state


def _show(qtbot, tab: AnalyticsTab) -> None:
    tab.show()
    qtbot.waitExposed(tab)


def test_the_card_shows_the_live_count_and_its_growth(qtbot, knowledge):
    tab = knowledge["make"]()
    _show(qtbot, tab)

    assert tab.card_known_words.value_label.text() == "1,250"
    caption = tab.known_words_caption.text()
    assert "Words Anki Miner treats as known in 日本語." in caption
    assert f"Change since {(TODAY - timedelta(days=10)).isoformat()}: +50" in caption


def test_a_hidden_page_scans_nothing(knowledge):
    tab = knowledge["make"]()

    tab.refresh_data(force=True)

    assert knowledge["calls"] == []


def test_the_scan_runs_once_per_window_and_again_after_a_run(qtbot, knowledge):
    tab = knowledge["make"]()
    _show(qtbot, tab)
    assert knowledge["calls"] == ["ja"]

    tab.refresh_data(force=True)
    assert knowledge["calls"] == ["ja"]

    tab.mark_stale()
    assert knowledge["calls"] == ["ja", "ja"]


def test_a_language_switch_scans_again(qtbot, knowledge):
    tab = knowledge["make"]()
    _show(qtbot, tab)

    tab.set_content_style(get_profile("ja").content_style)
    tab.refresh_data(force=True)

    assert knowledge["calls"] == ["ja", "ja"]


def test_a_scan_of_the_previous_language_is_dropped(qtbot, knowledge, test_config):
    """Review Focus 5: a scan that started before a language switch never shows."""
    tab = knowledge["make"]()
    _show(qtbot, tab)
    es_config = replace(test_config, language="es")
    tab._config_provider = lambda: es_config
    tab.set_content_style(get_profile("es").content_style)
    knowledge["bundle"] = _bundle(language="es", known_count=7, history=[])

    # The ja scan that was in flight during the switch lands now.
    tab._knowledge_in_flight = True
    tab._on_knowledge_done(_bundle(language="ja", known_count=999))

    assert knowledge["calls"][-1] == "es"
    assert tab.card_known_words.value_label.text() == "7"


def test_anki_down_shows_the_last_count(qtbot, knowledge):
    knowledge["bundle"] = _bundle(
        known_count=None, history=[KnownSizePoint(day=date(2026, 10, 1), word_count=40)], anki_error="down"
    )
    tab = knowledge["make"]()
    _show(qtbot, tab)

    assert tab.card_known_words.value_label.text() == "40"
    assert "Last counted on 2026-10-01; Anki was not reachable." in tab.known_words_caption.text()


def test_the_last_recorded_count_shows_before_the_scan(knowledge):
    tab = knowledge["make"]()
    tab.stats_service.get_known_size_history.return_value = [KnownSizePoint(day=date(2026, 10, 1), word_count=40)]

    tab.refresh_data(force=True)

    assert not tab.card_known_words.isHidden()
    assert tab.card_known_words.value_label.text() == "40"
    assert "Last counted on 2026-10-01." in tab.known_words_caption.text()


def test_no_count_at_all_hides_the_card(qtbot, knowledge):
    knowledge["bundle"] = _bundle(known_count=None, history=[])
    tab = knowledge["make"]()
    _show(qtbot, tab)

    assert tab.card_known_words.isHidden()
    assert tab.known_words_caption.isHidden()


def test_a_failed_scan_is_a_banner_with_retry(qtbot, knowledge):
    knowledge["error"] = RuntimeError("stats.db is locked")
    tab = knowledge["make"]()
    _show(qtbot, tab)

    issue = tab.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "Known words could not be counted."
    assert issue.action_text == "Retry"


def test_a_retry_that_lands_clears_the_banner(qtbot, knowledge):
    knowledge["error"] = RuntimeError("stats.db is locked")
    tab = knowledge["make"]()
    _show(qtbot, tab)
    knowledge["error"] = None
    scans = len(knowledge["calls"])

    tab.issue_banner().action_button.click()

    assert len(knowledge["calls"]) == scans + 1
    assert tab.issue_banner().current_issue() is None


def _coverage() -> FrequencyCoverage:
    return FrequencyCoverage(
        source_name="JPDB",
        bands=(
            CoverageBand(low=1, high=1000, known=1, missing=((2, "犬"),)),
            CoverageBand(low=1001, high=2000, known=3, missing=()),
            CoverageBand(low=2001, high=5000, known=0, missing=((3000, "鳥"), (4000, "魚"))),
            CoverageBand(low=5001, high=10000, known=0, missing=()),
            CoverageBand(low=10001, high=20000, known=0, missing=()),
        ),
    )


def test_the_table_lists_each_band(qtbot, knowledge):
    knowledge["bundle"] = _bundle(coverage=_coverage(), has_numeric_source=True)
    tab = knowledge["make"]()
    _show(qtbot, tab)

    assert tab._coverage_card.isVisible()
    table = tab.coverage_table
    assert table.rowCount() == 5
    assert [table.item(0, column).text() for column in range(4)] == ["1–1,000", "1", "1", "50.0%"]
    assert table.item(3, 3).text() == "—"
    assert "JPDB" in tab.coverage_source_label.text()


def test_show_missing_words_lists_the_band(qtbot, knowledge):
    knowledge["bundle"] = _bundle(coverage=_coverage(), has_numeric_source=True)
    tab = knowledge["make"]()
    _show(qtbot, tab)
    assert not tab.show_missing_button.isEnabled()

    tab.coverage_table.selectRow(2)
    assert tab.show_missing_button.isEnabled()
    tab.show_missing_button.click()

    dialog = tab._missing_dialog
    assert dialog is not None
    qtbot.addWidget(dialog)
    assert dialog.words_view.toPlainText() == "3000\t鳥\n4000\t魚"
    dialog.close()


def test_a_band_with_nothing_missing_disables_the_button(qtbot, knowledge):
    knowledge["bundle"] = _bundle(coverage=_coverage(), has_numeric_source=True)
    tab = knowledge["make"]()
    _show(qtbot, tab)

    tab.coverage_table.selectRow(1)

    assert not tab.show_missing_button.isEnabled()


def test_no_numeric_source_hides_the_section(qtbot, knowledge):
    knowledge["bundle"] = _bundle(coverage=None, has_numeric_source=False)
    tab = knowledge["make"]()
    _show(qtbot, tab)

    assert not tab._coverage_card.isVisible()


def test_anki_down_explains_instead_of_a_table(qtbot, knowledge):
    knowledge["bundle"] = _bundle(known_count=None, coverage=None, has_numeric_source=True, anki_error="down")
    tab = knowledge["make"]()
    _show(qtbot, tab)

    assert tab._coverage_card.isVisible()
    assert tab.coverage_status_label.isVisible()
    assert tab.coverage_status_label.text() == "Coverage needs Anki running: it compares the list with your cards."
    assert not tab.coverage_table.isVisible()

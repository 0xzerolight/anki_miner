"""AnalyticsTab refreshes its SQLite-backed stats off the GUI thread (OVH).

The four stats queries (get_overall_stats / get_recent_sessions /
get_series_difficulty / get_milestones) must run on a worker thread; only the
widget render lands back on the GUI thread. An in-flight guard stops overlapping
refreshes from stacking, and the error path clears that guard.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.models.stats import OverallStats

_MAIN_THREAD_ID = threading.get_ident()


def _empty_stats() -> OverallStats:
    return OverallStats(
        total_sessions=0,
        total_cards_created=0,
        total_words_encountered=0,
        total_unknown_words=0,
        series_count=0,
    )


def _make_service() -> MagicMock:
    service = MagicMock()
    service.is_available.return_value = True
    service.get_overall_stats.return_value = _empty_stats()
    service.get_recent_sessions.return_value = []
    service.get_series_difficulty.return_value = []
    service.get_milestones.return_value = []
    return service


def test_queries_run_off_gui_thread(qtbot):
    """Every stats query executes on a non-GUI thread."""
    service = _make_service()
    seen_threads: list[int] = []

    def _record(*_a, **_kw):
        seen_threads.append(threading.get_ident())
        return _empty_stats()

    def _record_list(*_a, **_kw):
        seen_threads.append(threading.get_ident())
        return []

    service.get_overall_stats.side_effect = _record
    service.get_recent_sessions.side_effect = _record_list
    service.get_series_difficulty.side_effect = _record_list
    service.get_milestones.side_effect = _record_list

    tab = AnalyticsTab(service)
    qtbot.addWidget(tab)
    try:
        tab.refresh_data(force=True)
        qtbot.waitUntil(lambda: tab._last_refresh is not None, timeout=3000)
        assert seen_threads, "no query ran"
        assert all(tid != _MAIN_THREAD_ID for tid in seen_threads), seen_threads
    finally:
        tab.deleteLater()


def test_render_happens_on_gui_thread(qtbot):
    """The render half (table populate) runs on the GUI thread after the fetch."""
    service = _make_service()
    render_threads: list[int] = []

    tab = AnalyticsTab(service)
    qtbot.addWidget(tab)
    orig = tab._update_dashboard

    def _spy(stats):
        render_threads.append(threading.get_ident())
        return orig(stats)

    tab._update_dashboard = _spy  # type: ignore[method-assign]
    try:
        tab.refresh_data(force=True)
        qtbot.waitUntil(lambda: bool(render_threads), timeout=3000)
        assert render_threads == [_MAIN_THREAD_ID]
    finally:
        tab.deleteLater()


def test_in_flight_guard_prevents_overlapping_dispatch(qtbot):
    """A second refresh_data while one is in flight must not dispatch again."""
    service = _make_service()
    tab = AnalyticsTab(service)
    qtbot.addWidget(tab)
    try:
        tab.refresh_data(force=True)
        # Synchronously (before the worker can finish) fire a second forced
        # refresh. The in-flight guard must drop it.
        assert tab._refresh_in_flight is True
        tab.refresh_data(force=True)
        qtbot.waitUntil(lambda: tab._last_refresh is not None, timeout=3000)
        # Only one batch of queries ran despite two refresh calls.
        assert service.get_overall_stats.call_count == 1
        assert tab._refresh_in_flight is False
    finally:
        tab.deleteLater()


def test_error_path_clears_in_flight_and_logs(qtbot, caplog):
    """A failing query clears the in-flight flag and logs (no crash, no TTL tick)."""
    service = _make_service()
    service.get_overall_stats.side_effect = RuntimeError("db gone")

    tab = AnalyticsTab(service)
    qtbot.addWidget(tab)
    try:
        with caplog.at_level("ERROR", logger="anki_miner.gui.widgets.analytics_tab"):
            tab.refresh_data(force=True)
            qtbot.waitUntil(lambda: tab._refresh_in_flight is False, timeout=3000)
        assert tab._last_refresh is None, "errored refresh must not tick the TTL clock"
        assert any("db gone" in rec.getMessage() for rec in caplog.records)
        # Guard cleared, so a subsequent refresh can dispatch.
        service.get_overall_stats.side_effect = None
        tab.refresh_data(force=True)
        qtbot.waitUntil(lambda: tab._last_refresh is not None, timeout=3000)
    finally:
        tab.deleteLater()


def test_unavailable_service_short_circuits(qtbot):
    """is_available() False keeps the old short-circuit (no dispatch)."""
    service = _make_service()
    service.is_available.return_value = False
    tab = AnalyticsTab(service)
    qtbot.addWidget(tab)
    try:
        tab.refresh_data(force=True)
        assert tab._refresh_in_flight is False
        assert service.get_overall_stats.call_count == 0
    finally:
        tab.deleteLater()

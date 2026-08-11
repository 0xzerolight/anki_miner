"""Production wiring for Settings resource-release callbacks."""

from __future__ import annotations

from unittest.mock import MagicMock

from anki_miner.gui.workers.backfill_worker import BackfillScanWorker


def test_all_indexed_resource_panels_share_window_release_callback(wired_window) -> None:
    window, _titles, tabs = wired_window
    settings_tab = tabs["Settings"]
    expected = window.release_dictionary_resources

    for panel in (
        settings_tab.dictionary_panel,
        settings_tab.frequency_panel,
        settings_tab.audio_panel,
    ):
        callback = panel._release_callback
        assert callback is not None
        assert callback.__self__ is expected.__self__
        assert callback.__func__ is expected.__func__


def test_running_prewarm_refuses_before_tab_fanout(wired_window, monkeypatch) -> None:
    window, _titles, tabs = wired_window
    release = MagicMock(return_value=True)
    monkeypatch.setattr(tabs["Video"], "release_dictionary_resources", release)
    prewarm_worker = MagicMock()
    prewarm_worker.isRunning.return_value = True
    window.background_tasks.prewarm_worker = prewarm_worker

    assert window.release_dictionary_resources() is False
    release.assert_not_called()


def test_running_backfill_scan_refuses_window_fanout(wired_window) -> None:
    window, _titles, tabs = wired_window
    worker = MagicMock(spec=BackfillScanWorker)
    worker.isRunning.return_value = True
    tabs["Utilities"].backfill_tab.worker_thread = worker

    assert window.release_dictionary_resources() is False


def test_tools_download_holds_dictionary_mutation_until_session_releases(wired_window, monkeypatch) -> None:
    from anki_miner.gui.widgets.dialogs import resource_download_dialog

    window, _titles, tabs = wired_window
    panel = tabs["Settings"].dictionary_panel
    observed: list[bool] = []

    def fake_start(_parent, _config, **kwargs):
        config, release = kwargs["acquire_mutation"]()
        assert config is window.config
        observed.append(panel.has_active_mutation("resource-download"))
        release()
        observed.append(panel.has_active_mutation("resource-download"))
        return MagicMock(task_id=resource_download_dialog.TASK_ID)

    monkeypatch.setattr(resource_download_dialog, "start_resource_download", fake_start)
    monkeypatch.setattr(window.background_tasks, "cancel_jmdict_migration", lambda: None)

    window._download_recommended_resources()

    assert observed == [True, False]


def test_tools_download_busy_state_uses_main_issue_banner(wired_window, monkeypatch) -> None:
    from anki_miner.gui.widgets.dialogs import resource_download_dialog

    window, _titles, _tabs = wired_window

    def fake_start(_parent, _config, **kwargs):
        kwargs["blocked"]("Indexed resources are in use.")
        return None

    monkeypatch.setattr(resource_download_dialog, "start_resource_download", fake_start)
    monkeypatch.setattr(window.background_tasks, "cancel_jmdict_migration", lambda: None)

    window._download_recommended_resources()

    issue = window.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "Indexed resources are in use."
    assert issue.action_id == "resource-download.retry"


def test_resource_download_retry_clears_its_issue_after_start(wired_window, monkeypatch) -> None:
    from anki_miner.gui.widgets.dialogs import resource_download_dialog

    window, _titles, _tabs = wired_window
    session = MagicMock(task_id=resource_download_dialog.TASK_ID)
    attempts = 0

    def fake_start(_parent, _config, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            kwargs["blocked"]("Indexed resources are in use.")
            return None
        return session

    monkeypatch.setattr(resource_download_dialog, "start_resource_download", fake_start)
    monkeypatch.setattr(window.background_tasks, "cancel_jmdict_migration", lambda: None)

    window._download_recommended_resources()
    banner = window.issue_banner()
    assert banner.current_issue() is not None

    banner.action_button.click()

    assert attempts == 2
    assert window._resource_download_session is session
    assert banner.current_issue() is None


def test_activation_clears_only_matching_resource_download_issue(wired_window, monkeypatch) -> None:
    from anki_miner.gui.widgets.base import ScreenIssue
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadResult, ResourceDownloadSummary

    window, _titles, _tabs = wired_window
    summary = ResourceDownloadSummary(
        results=[ResourceDownloadResult("dict", "dict", "Dictionary", "u", True, "10 entries", dict_id="dict")]
    )
    monkeypatch.setattr(
        "anki_miner.gui.utils.resource_setup.apply_download_summary",
        lambda config, _summary: config,
    )
    monkeypatch.setattr(window, "update_config", lambda _config: None)

    window._show_resource_download_blocked("Indexed resources are in use.")
    assert window._activate_downloaded_resources(summary) is window.config
    assert window.issue_banner().current_issue() is None

    other_issue = ScreenIssue(summary="Another failure.", action_id="other.retry", action_text="Retry")
    window.show_screen_issue(other_issue)
    assert window._activate_downloaded_resources(summary) is window.config
    assert window.issue_banner().current_issue() is other_issue

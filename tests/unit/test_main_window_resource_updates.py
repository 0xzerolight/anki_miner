"""MainWindow's dictionary updates: the weekly automatic run and Update Dictionaries Now."""

from __future__ import annotations

import os
import time
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.config import ChainEntry
from anki_miner.gui.main_window import MainWindow
from anki_miner.services import resource_updates as ru

# Captured at import, before the autouse seam in tests/conftest.py no-ops it.
_REAL_AUTO = MainWindow._maybe_auto_update_resources

_CHAIN_J = (ChainEntry(kind="indexed", dict_id="j", enabled=True),)


def _inline_off_thread(parent, work, on_done, on_error=None, *, pass_cancel_check=False, on_finished=None, **_kw):
    try:
        result = work(lambda: False) if pass_cancel_check else work()
    except Exception as exc:  # noqa: BLE001
        if on_error is not None:
            on_error(str(exc))
    else:
        on_done(result)
    finally:
        if on_finished is not None:
            on_finished()


@pytest.fixture
def window(qtbot, monkeypatch, patch_heavy_init, test_config):
    patch_heavy_init(replace(test_config, first_run_setup_done=True), stub_first_run_setup=False)
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module, "run_off_thread", _inline_off_thread)
    win = MainWindow()
    qtbot.addWidget(win)
    win.status_lines = []
    monkeypatch.setattr(
        win.status_bar, "set_operation", lambda text, level="info", **_k: win.status_lines.append((text, level))
    )
    yield win
    win.deleteLater()


@pytest.fixture
def migrating(window, monkeypatch):
    """The startup JMdict migration is running.

    Patched at the query rather than by planting a fake worker: pytest-qt
    closes the window (joining real workers) before fixture finalizers run.
    """
    monkeypatch.setattr(window.background_tasks, "jmdict_migration_running", lambda: True)


def _stamp():
    from anki_miner.gui.utils.runtime_state import resource_update_stamp

    return resource_update_stamp()


def _resource(slot_id="j"):
    return ru.UpdatableResource("dict", slot_id, "Jitendex", "1", "https://x/i.json", "https://x/d.zip")


def _spec():
    return ru.ResourceUpdate(_resource(), "Jitendex 2", "2", "https://x/d.zip").to_spec()


def _check(monkeypatch, result):
    monkeypatch.setattr(ru, "updatable_resources", lambda config: [_resource()])
    monkeypatch.setattr(ru, "check_for_updates", lambda resources, **kw: result)


def _summary(*results, cancelled=False, blocked=False):
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary

    return ResourceDownloadSummary(
        results=list(results), requested_count=len(results), cancelled=cancelled, blocked=blocked
    )


def _result(ok=True):
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadResult

    return ResourceDownloadResult("j", "dict", "Jitendex", "u", ok, "1 entries" if ok else "broken", dict_id="j")


def _start_task(window, title="Recommended resources"):
    from anki_miner.gui.capabilities import CapabilityTarget
    from anki_miner.gui.controllers.task_registry import TaskSpec
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod

    return window.task_registry.start(
        TaskSpec(task_id=dialog_mod.TASK_ID, title=title, owner=CapabilityTarget("settings", "dictionaries"))
    )


def test_auto_run_respects_the_setting(window, monkeypatch):
    window.config = replace(window.config, auto_update_dictionaries=False)
    started = MagicMock()
    monkeypatch.setattr(window, "_start_resource_update_check", started)
    _REAL_AUTO(window)
    started.assert_not_called()


def test_auto_run_waits_a_week(window, monkeypatch):
    started = MagicMock()
    monkeypatch.setattr(window, "_start_resource_update_check", started)
    ru.mark_update_checked(_stamp())
    _REAL_AUTO(window)
    started.assert_not_called()
    eight_days_ago = time.time() - 8 * 24 * 3600
    os.utime(_stamp(), (eight_days_ago, eight_days_ago))
    _REAL_AUTO(window)
    started.assert_called_once_with(manual=False)


def test_auto_run_never_starts_beside_the_jmdict_migration(window, migrating, monkeypatch):
    started = MagicMock()
    monkeypatch.setattr(window, "_start_resource_update_check", started)
    _REAL_AUTO(window)
    started.assert_not_called()


def test_prewarm_finishing_starts_the_weekly_check(window, monkeypatch, qtbot):
    from PyQt6.QtCore import QThread

    from anki_miner.gui.workers import prewarm_worker as prewarm_module

    class _Done(QThread):
        def __init__(self, config):
            super().__init__()

        def run(self):
            pass

    monkeypatch.setattr(prewarm_module, "PrewarmWorker", _Done)
    calls = []
    monkeypatch.setattr(MainWindow, "_maybe_auto_update_resources", lambda self: calls.append(self))
    window._start_prewarm()
    thread = window.background_tasks.prewarm_worker
    qtbot.waitUntil(lambda: calls == [window], timeout=3000)
    if thread is not None:
        thread.wait(1000)


def test_up_to_date_stamps_and_says_so_when_asked(window, monkeypatch):
    _check(monkeypatch, ru.UpdateCheck(checked=1))
    window.update_resources_now()
    assert _stamp().exists()
    assert window.status_lines[-1] == ("Dictionaries are up to date.", "success")


def test_nothing_updatable_says_so(window, monkeypatch):
    monkeypatch.setattr(ru, "updatable_resources", lambda config: [])
    window.update_resources_now()
    assert window.status_lines[-1] == ("None of your dictionaries publish updates.", "info")


def test_an_offline_automatic_check_leaves_no_stamp_and_no_message(window, monkeypatch):
    _check(monkeypatch, ru.UpdateCheck(checked=1, failures=((_resource(), "offline"),)))
    window._start_resource_update_check(manual=False)
    assert not _stamp().exists()
    assert window.status_lines == []


def test_found_updates_start_one_update_run(window, monkeypatch):
    window.config = replace(window.config, dictionary_chain=_CHAIN_J)
    _check(
        monkeypatch,
        ru.UpdateCheck(checked=1, updates=(ru.ResourceUpdate(_resource(), "J2", "2", "https://x/d.zip"),)),
    )
    start = MagicMock()
    monkeypatch.setattr(window, "_start_resource_update", start)
    window._start_resource_update_check(manual=False)
    (specs,), kwargs = start.call_args
    assert [s.id for s in specs] == ["j"] and kwargs == {"manual": False}


def test_a_language_switch_during_the_check_installs_nothing(window, monkeypatch):
    window.config = replace(window.config, language="ja", dictionary_chain=_CHAIN_J)
    monkeypatch.setattr(ru, "updatable_resources", lambda config: [_resource()])

    def check_then_switch(resources, **kw):
        window.config = replace(window.config, language="zh")
        return ru.UpdateCheck(checked=1, updates=(ru.ResourceUpdate(_resource(), "J2", "2", "https://x/d.zip"),))

    monkeypatch.setattr(ru, "check_for_updates", check_then_switch)
    start = MagicMock()
    monkeypatch.setattr(window, "_start_resource_update", start)
    window._start_resource_update_check(manual=False)
    start.assert_not_called()
    assert not _stamp().exists()


def test_a_manual_click_during_the_automatic_check_gets_the_answer(window, monkeypatch):
    from anki_miner.gui import main_window as mw_module

    pending = {}
    monkeypatch.setattr(
        mw_module, "run_off_thread", lambda parent, work, on_done, *a, **kw: pending.update(on_done=on_done)
    )
    window._start_resource_update_check(manual=False)
    window.update_resources_now()  # the automatic check is still running
    pending["on_done"](ru.UpdateCheck(checked=1))
    assert window.status_lines[-1] == ("Dictionaries are up to date.", "success")


@pytest.mark.parametrize("manual", [False, True])
def test_a_blocked_update_run_is_silent_only_when_automatic(window, monkeypatch, manual):
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod

    seen = {}

    def fake_start(parent, config, **kwargs):
        seen.update(kwargs)
        kwargs["blocked"]("busy")
        return None

    monkeypatch.setattr(dialog_mod, "start_resource_download", fake_start)
    issues = MagicMock()
    monkeypatch.setattr(window, "show_screen_issue", issues)
    window._start_resource_update([_spec()], manual=manual)
    assert seen["show_window"] is manual and seen["title"] == "Dictionary updates"
    assert seen["window_on_reveal"] is True
    assert issues.called is manual
    if manual:
        assert issues.call_args.args[0].action_id == "resource-update.retry"
    assert not _stamp().exists()


def test_the_automatic_run_waits_for_another_resource_run(window, monkeypatch):
    from anki_miner.gui.controllers.task_registry import TaskOutcome
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod

    handle = _start_task(window)
    start = MagicMock()
    monkeypatch.setattr(dialog_mod, "start_resource_download", start)
    try:
        window._start_resource_update([_spec()], manual=False)
        start.assert_not_called()
    finally:
        handle.finish(TaskOutcome.SUCCEEDED)


def test_update_now_during_a_live_resource_run_reveals_it_without_checking(window, monkeypatch):
    from anki_miner.gui.controllers.task_registry import TaskOutcome
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod

    handle = _start_task(window, title="Dictionary updates")
    check = MagicMock()
    monkeypatch.setattr(window, "_start_resource_update_check", check)
    reveal = MagicMock()
    monkeypatch.setattr(window.task_registry, "request_reveal", reveal)
    try:
        window.update_resources_now()
        reveal.assert_called_once_with(dialog_mod.TASK_ID)
        check.assert_not_called()
    finally:
        handle.finish(TaskOutcome.SUCCEEDED)


def test_the_update_run_never_cancels_the_jmdict_migration(window, migrating, monkeypatch):
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod

    start = MagicMock()
    monkeypatch.setattr(dialog_mod, "start_resource_download", start)
    cancel = MagicMock()
    monkeypatch.setattr(window.background_tasks, "cancel_jmdict_migration", cancel)
    window._start_resource_update([_spec()], manual=False)
    start.assert_not_called()
    cancel.assert_not_called()


def test_a_finished_automatic_run_stamps_and_names_what_it_updated(window):
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadOutcome

    window._on_resource_update_finished(
        ResourceDownloadOutcome(config=window.config, summary=_summary(_result()), activated=True), manual=False
    )
    assert _stamp().exists()
    assert window.status_lines[-1] == ("Dictionaries updated: Jitendex", "success")


def test_a_release_that_keeps_failing_is_retried_weekly_not_every_launch(window):
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadOutcome

    window._on_resource_update_finished(
        ResourceDownloadOutcome(config=window.config, summary=_summary(_result(ok=False)), activated=False),
        manual=False,
    )
    assert _stamp().exists()
    assert window.status_lines == []


@pytest.mark.parametrize("kwargs", [{"cancelled": True}, {"blocked": True}])
def test_a_cancelled_or_blocked_run_retries_next_launch(window, kwargs):
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadOutcome

    window._on_resource_update_finished(
        ResourceDownloadOutcome(config=window.config, summary=_summary(_result(), **kwargs), activated=True),
        manual=False,
    )
    assert not _stamp().exists()


def test_update_activation_keeps_the_chain_and_repaints_settings(window, monkeypatch):
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary

    summary = ResourceDownloadSummary(results=[_result()], requested_count=1, dicts_root=window.config.dicts_root)
    before = window.config
    applied, repainted = [], []
    monkeypatch.setattr(window, "update_config", lambda config, **_k: applied.append(config))
    monkeypatch.setattr(window, "_repaint_resource_chains", lambda: repainted.append(True))
    assert window._activate_resource_update(summary) is before
    assert applied == [before] and repainted == [True]

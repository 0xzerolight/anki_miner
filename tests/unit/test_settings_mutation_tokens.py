"""C2/J26 regression tests for settings mutation ownership."""

from __future__ import annotations

import contextlib
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry, ChainEntry, FreqEntry
from anki_miner.gui.controllers import import_flow_common as import_flow_common_module
from anki_miner.gui.controllers.background_tasks import BackgroundTaskController
from anki_miner.gui.utils.config_commit import ConfigCommitError, ConfigCommitResult
from anki_miner.gui.widgets.panels import chain_settings_panel_base as base_module
from anki_miner.gui.widgets.panels import frequency_settings_panel as frequency_panel_module
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.services._sqlite_index import write_ownership_marker


@pytest.fixture
def tab(test_config, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    widget.shutdown()
    for worker in widget.iter_close_workers():
        if worker is not None:
            worker.wait(3000)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


@pytest.mark.parametrize(
    ("flow_name", "panel_name"),
    [
        ("_dict_import_flow", "dictionary_panel"),
        ("_frequency_import_flow", "frequency_panel"),
        ("_audio_pack_import_flow", "audio_panel"),
    ],
)
def test_import_token_survives_refresh_rebuild(tab, qtbot, flow_name, panel_name):
    flow = getattr(tab, flow_name)
    panel = getattr(tab, panel_name)

    flow._set_import_buttons_enabled(False)
    panel.refresh_registry()
    qtbot.waitUntil(lambda: not panel._scan_in_flight, timeout=3000)

    assert not panel._list.isEnabled()
    assert not panel._remove_btn.isEnabled()
    assert not panel._list.dragEnabled()
    # The move controls live on the rows, so the gate has to reach every one of
    # them -- moving them off the toolbar must not move them out of the gate.
    assert all(not row.up_button.isEnabled() and not row.down_button.isEnabled() for row in panel._rows())

    flow._set_import_buttons_enabled(True)

    assert panel._list.isEnabled()
    assert panel._remove_btn.isEnabled()
    assert panel._list.dragEnabled()
    rows = panel._rows()
    # Boundary-aware once released: interior rows offer both directions.
    assert all(row.up_button.isEnabled() for row in rows[1:])
    assert all(row.down_button.isEnabled() for row in rows[:-1])


def test_named_tokens_are_ref_counted_and_release_is_idempotent(tab):
    panel = tab.dictionary_panel

    first = panel.hold_mutation("import")
    second = panel.hold_mutation("import")
    panel._rebuild_list()

    panel.release(first)
    panel.release(first)

    assert not panel._remove_btn.isEnabled()
    assert not panel.dicts_root_selector.isEnabled()

    panel.release(second)

    assert panel._remove_btn.isEnabled()
    assert panel.dicts_root_selector.isEnabled()


def test_context_menu_refuses_while_mutation_token_is_held(tab, monkeypatch):
    panel = tab.frequency_panel
    panel.set_chain((FreqEntry(source_id="source", enabled=True),), registry_meta={})
    constructed: list[None] = []

    class UnexpectedMenu:
        def __init__(self, *args, **kwargs):
            constructed.append(None)

    monkeypatch.setattr(frequency_panel_module, "QMenu", UnexpectedMenu)
    token = panel.hold_mutation("import")
    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()

    panel._on_row_context_menu(pos)

    panel.release(token)
    assert constructed == []


def test_remove_completion_rebases_on_current_chain(qtbot, monkeypatch, tmp_path):
    target = tmp_path / "remove-me"
    target.mkdir()
    write_ownership_marker(target, "remove-me", "dictionary")
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="remove-me", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    monkeypatch.setattr(panel, "_confirm_remove", lambda _display: True)
    remove_done = None

    def fake_run_off_thread(parent, work, on_done, on_error, **kwargs):
        nonlocal remove_done
        if remove_done is None:
            remove_done = on_done
            return None
        try:
            on_done(work())
        except Exception as exc:  # pragma: no cover - failure diagnostic
            on_error(str(exc))
        return None

    monkeypatch.setattr(base_module, "run_off_thread", fake_run_off_thread)

    panel.remove(0)
    assert remove_done is not None
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="remove-me", enabled=True),
            ChainEntry(kind="indexed", dict_id="added-during-remove", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    remove_done((True, None))

    assert [entry.dict_id for entry in panel.get_chain()] == ["added-during-remove", None]


def test_save_rereads_all_chains_after_mid_save_mutation(tab, monkeypatch):
    """A chain mutation landing mid-Save (async flow completion) must win over
    the start-of-Save snapshot — the commit re-reads every immediate-persist
    chain (incl. pitch) right before emitting."""
    from anki_miner.config import PitchSourceEntry

    tab.dictionary_panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="before", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    tab.frequency_panel.set_chain((FreqEntry(source_id="before", enabled=True),), registry_meta={})
    tab.audio_panel.set_chain(
        (AudioSourceEntry(kind="pack", pack_id="before", enabled=True),),
        registry_meta={},
    )
    tab.pitch_panel.set_chain((PitchSourceEntry(source_id="before", enabled=True),), registry_meta={})

    original_contribute = tab.anki_panel.contribute

    def contribute_with_mid_save_mutation(config):
        # Simulate an async import-flow completion arriving mid-fold.
        tab.dictionary_panel.set_chain(
            (
                ChainEntry(kind="indexed", dict_id="after", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            )
        )
        tab.frequency_panel.set_chain((FreqEntry(source_id="after", enabled=True),), registry_meta={})
        tab.audio_panel.set_chain(
            (AudioSourceEntry(kind="pack", pack_id="after", enabled=True),),
            registry_meta={},
        )
        tab.pitch_panel.set_chain((PitchSourceEntry(source_id="after", enabled=True),), registry_meta={})
        return original_contribute(config)

    monkeypatch.setattr(tab.anki_panel, "contribute", contribute_with_mid_save_mutation)
    committed = []
    tab.config_changed.connect(committed.append)

    tab.commit_settings()

    assert committed[-1].dictionary_chain[0].dict_id == "after"
    assert committed[-1].frequency_chain[0].source_id == "after"
    assert committed[-1].expression_audio_chain[0].pack_id == "after"
    assert committed[-1].pitch_chain[0].source_id == "after"


def test_mutation_preflight_refuses_without_consuming_pending_root(tab, tmp_path):
    new_root = tmp_path / "new-root"
    new_root.mkdir()
    tab.dictionary_panel.dicts_root_selector.set_path(str(new_root))
    assert tab._debounce_timer.isActive()
    token = tab.dictionary_panel.hold_mutation("import")

    accepted = tab.commit_pending_settings_for_mutation()

    assert accepted is False
    assert tab._debounce_timer.isActive()
    assert tab.dictionary_panel.dicts_root_selector.get_path() == str(new_root)
    tab.dictionary_panel.release(token)


def test_mutation_preflight_refuses_when_config_commit_fails(test_config, qtbot, tmp_path):
    def fail_commit(_config):
        raise OSError("disk full")

    widget = SettingsTab(test_config, commit_config=fail_commit)
    qtbot.addWidget(widget)
    old_panel_root = widget.dictionary_panel._dicts_root
    new_root = tmp_path / "new-root"
    new_root.mkdir()
    widget.dictionary_panel.dicts_root_selector.set_path(str(new_root))

    try:
        assert widget.commit_pending_settings_for_mutation() is False
        assert widget._debounce_timer.isActive()
        assert widget.config.dicts_root == test_config.dicts_root
        assert widget.dictionary_panel.dicts_root_selector.get_path() == str(new_root)
        assert widget.dictionary_panel._dicts_root == old_panel_root
    finally:
        widget.shutdown()
        for worker in widget.iter_close_workers():
            if worker is not None:
                worker.wait(3000)


def test_mutation_preflight_commits_dirty_settings_without_pitch_selector(test_config, qtbot):
    """Regression (17b): the shared preflight ran an unconditional
    pitch_accent_selector read whenever _settings_dirty was True — with the
    selector removed, a dirty preflight from ANY panel's Add/Reimport must
    commit cleanly and return True, not AttributeError."""
    committed: list[AnkiMinerConfig] = []
    widget = SettingsTab(test_config, commit_config=committed.append)
    qtbot.addWidget(widget)
    widget.anki_panel.set_deck_name("PreflightDeck")
    assert widget._settings_dirty is True

    try:
        assert widget.commit_pending_settings_for_mutation() is True
        assert committed and committed[-1].anki_deck_name == "PreflightDeck"
    finally:
        widget.shutdown()
        for worker in widget.iter_close_workers():
            if worker is not None:
                worker.wait(3000)


def test_post_save_removal_sync_prevents_later_chain_resurrection(
    test_config,
    qtbot,
) -> None:
    persisted: list[AnkiMinerConfig] = []

    def commit(config: AnkiMinerConfig) -> None:
        persisted.append(config)
        if len(persisted) == 1:
            raise ConfigCommitError(ConfigCommitResult.post_save_failure(RuntimeError("refresh failed")))

    config = replace(
        test_config,
        dictionary_chain=(
            ChainEntry(kind="indexed", dict_id="remove-me", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        ),
    )
    widget = SettingsTab(config, commit_config=commit)
    qtbot.addWidget(widget)
    try:
        result = widget._commit_dictionary_removal((ChainEntry(kind="jisho", dict_id=None, enabled=True),))
        widget._persist_audio_chain_change(widget.config.expression_audio_chain)

        assert result.persisted is True
        assert [entry.kind for entry in widget.config.dictionary_chain] == ["jisho"]
        assert [entry.kind for entry in persisted[-1].dictionary_chain] == ["jisho"]
    finally:
        widget.shutdown()
        for worker in widget.iter_close_workers():
            if worker is not None:
                worker.wait(3000)


def test_root_edit_is_committed_before_immediate_add(tab, monkeypatch, tmp_path):
    new_root = tmp_path / "new-root"
    new_root.mkdir()
    tab.config_changed.connect(tab.update_config)
    tab.dictionary_panel.dicts_root_selector.set_path(str(new_root))
    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.file_dialogs.pick_open_files",
        lambda *args, on_done, **kwargs: on_done([str(tmp_path / "dictionary.zip")]),
    )
    captured_roots = []

    def fake_for_yomitan(zip_path, root, **kwargs):
        captured_roots.append(root)
        return object()

    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.ImportWorker.for_yomitan",
        fake_for_yomitan,
    )

    def fake_run_chained_imports(**kwargs):
        kwargs["make_worker"](kwargs["jobs"][0])
        tab._dict_import_flow._set_import_buttons_enabled(True)

    monkeypatch.setattr(tab._dict_import_flow, "_run_chained_imports", fake_run_chained_imports)

    tab._dict_import_flow.add_dict()

    assert captured_roots == [new_root]
    assert tab.config.dicts_root == new_root


@pytest.mark.parametrize(
    "entry_point",
    ["add", "reimport", "reimport-all", "remove", "restore"],
)
def test_retained_migration_worker_refuses_every_settings_dictionary_entry_point(
    tab,
    monkeypatch,
    entry_point,
):
    class RetainedMigrationWorker:
        def __init__(self) -> None:
            self.cancel_calls = 0
            self.wait_calls: list[int] = []

        def isRunning(self) -> bool:  # noqa: N802
            return True

        def cancel(self) -> None:
            self.cancel_calls += 1

        def wait(self, timeout_ms: int) -> bool:
            self.wait_calls.append(timeout_ms)
            return False

    controller = BackgroundTaskController(tab)  # type: ignore[arg-type]
    worker = RetainedMigrationWorker()
    controller.jmdict_migration_worker = worker  # type: ignore[assignment]
    tab.set_dictionary_mutation_preflight(controller.prepare_dictionary_mutation)
    tab.dictionary_panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="slot", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    unexpected = MagicMock(side_effect=AssertionError("mutation continued after refused preflight"))
    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.file_dialogs.pick_open_file",
        unexpected,
    )
    # Add opens the multi-select picker; both must stay unreached.
    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.file_dialogs.pick_open_files",
        unexpected,
    )
    monkeypatch.setattr(tab.dictionary_panel, "_confirm_remove", unexpected)
    monkeypatch.setattr(tab._dict_import_flow, "_run_latest_scan", unexpected)

    if entry_point == "add":
        tab._dict_import_flow.add_dict()
    elif entry_point == "reimport":
        tab._dict_import_flow.reimport_dict("slot")
    elif entry_point == "reimport-all":
        tab._dict_import_flow.reimport_all()
    elif entry_point == "remove":
        tab.dictionary_panel.remove(0)
    else:
        tab._dict_import_flow.restore_unlisted()

    assert worker.cancel_calls == 1
    assert worker.wait_calls == [1000]
    assert controller.jmdict_migration_worker is worker
    unexpected.assert_not_called()


def test_scan_dispatch_failure_releases_mutation_token(tab, monkeypatch):
    flow = tab._frequency_import_flow
    flow._set_import_buttons_enabled(False)
    errors = []

    def fail_dispatch(*args, **kwargs):
        raise RuntimeError("dispatch failed")

    def on_error(message):
        errors.append(message)
        flow._set_import_buttons_enabled(True)

    monkeypatch.setattr(import_flow_common_module, "run_off_thread", fail_dispatch)

    flow._run_latest_scan(lambda: None, lambda _result: None, on_error)

    assert errors == ["dispatch failed"]
    assert flow._mutation_token is None
    assert tab.frequency_panel._add_btn.isEnabled()


def test_modal_worker_start_failure_releases_mutation_token(tab):
    flow = tab._dict_import_flow
    worker = MagicMock()
    worker.isRunning.return_value = False
    worker.start.side_effect = RuntimeError("start failed")
    flow._set_import_buttons_enabled(False)

    with pytest.raises(RuntimeError, match="start failed"):
        flow._run_modal_import(
            worker=worker,
            progress_label="Working",
            cancel_label="Cancel",
            determinate=True,
            join_noun="test worker",
            failure_summary="Failed",
            refusal_message="Busy",
            cancelling_label="Cancelling",
            missing_result_message="No result",
            trace_id="test",
            on_success=lambda _resource_id, _meta: None,
        )

    assert flow._mutation_token is None
    assert flow._active_import_worker is None
    assert tab.dictionary_panel._add_btn.isEnabled()

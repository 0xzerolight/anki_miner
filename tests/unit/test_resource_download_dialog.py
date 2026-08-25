"""Tests for the background resource-download session.

The flow used to be an ``ApplicationModal`` progress dialog driven by a nested
``QEventLoop``. The lifecycle and cancellation cases proved there are carried
over verbatim in intent — worker survival, deferred staging cleanup, the native
finish barrier, the locked Cancel — and restated against the modeless session.
New cases cover what the old flow could not express: Hide, the task strip, and
the split between *imported* and *installed*.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QLocale, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QLabel, QWidget

from anki_miner.config import create_default_config
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets.dialogs import resource_download_dialog as mod
from anki_miner.gui.widgets.dialogs.resource_download_dialog import (
    ResourceDownloadOutcome,
    ResourceDownloadSession,
    result_headline,
    result_lines,
    start_resource_download,
)
from anki_miner.gui.workers.resource_download_worker import (
    ResourceDownloadResult,
    ResourceDownloadSummary,
    ResourcePhase,
    ResourceProgress,
)
from anki_miner.services.resource_catalog import ResourceSpec

MOD = "anki_miner.gui.widgets.dialogs.resource_download_dialog"

_DL = ResourcePhase.DOWNLOADING
_INSTALL = ResourcePhase.INSTALLING
_INDEX = ResourcePhase.INDEXING


def _progress(phase: ResourcePhase, spec_id: str = "jitendex", name: str = "Jitendex", **kwargs) -> ResourceProgress:
    return ResourceProgress(spec_id=spec_id, display_name=name, phase=phase, **kwargs)


def _successful_summary() -> ResourceDownloadSummary:
    return ResourceDownloadSummary(
        results=[
            ResourceDownloadResult(
                spec_id="jitendex",
                kind="dict",
                display_name="Jitendex",
                url="https://example.invalid/jitendex.zip",
                ok=True,
                detail="10 entries",
                dict_id="jitendex",
            )
        ]
    )


class _FakeWorker(QThread):
    """A worker that emits exactly what a test asks for, then parks.

    Parking on ``release_native`` is what lets a test observe the window while
    the thread is genuinely still alive — the distinction the terminal barrier
    turns on.
    """

    item_progress = pyqtSignal(object)
    item_done = pyqtSignal(str, bool, str)
    finished_summary = pyqtSignal(object)

    def __init__(self, summary: ResourceDownloadSummary | None = None, *, events: list | None = None) -> None:
        super().__init__()
        self.summary = summary
        self.events = list(events or [])
        self.cancel_calls = 0
        self.started_event = threading.Event()
        self.progress_done = threading.Event()
        self.summary_emitted = threading.Event()
        self.release_native = threading.Event()

    def cancel(self) -> None:
        self.cancel_calls += 1

    def run(self) -> None:
        self.started_event.set()
        for event in self.events:
            self.item_progress.emit(event)
        self.progress_done.set()
        if self.summary is not None:
            self.finished_summary.emit(self.summary)
            self.summary_emitted.set()
        self.release_native.wait(5.0)


@pytest.fixture
def parent(qtbot) -> QWidget:
    widget = QWidget()
    qtbot.addWidget(widget)
    return widget


def _start(
    monkeypatch,
    tmp_path: Path,
    parent: QWidget,
    worker: _FakeWorker,
    *,
    activate=None,
    registry: TaskRegistry | None = None,
    adopt=None,
    release=None,
    acquire=None,
    clock=None,
) -> tuple[ResourceDownloadSession, Path]:
    download_dir = tmp_path / "download"
    download_dir.mkdir(exist_ok=True)
    (download_dir / "in-flight.part").write_bytes(b"partial")
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda **_kwargs: str(download_dir))
    monkeypatch.setattr(mod, "ResourceDownloadWorker", lambda *a, **kw: worker)
    extra = {} if clock is None else {"clock": clock}
    if acquire is not None:
        extra["acquire_mutation"] = acquire
    session = ResourceDownloadSession(
        parent,
        create_default_config(),
        activate=activate if activate is not None else (lambda _summary: None),
        release_resources=release,
        task_registry=registry,
        adopt_worker=adopt,
        **extra,
    )
    assert session.start()
    return session, download_dir


def _drain(qtbot, worker: _FakeWorker) -> None:
    worker.release_native.set()
    assert QThread.wait(worker, 3000)
    qtbot.wait(20)


# ---------------------------------------------------------------------------
# Blocked launch (ported)
# ---------------------------------------------------------------------------


def test_release_false_aborts_without_downloading(parent, monkeypatch):
    built = MagicMock()
    monkeypatch.setattr(mod, "ResourceDownloadWorker", built)
    parent.status_label = QLabel(parent)

    with patch("PyQt6.QtWidgets.QMessageBox.warning") as warn:
        session = start_resource_download(
            parent,
            create_default_config(),
            activate=lambda _s: None,
            release_resources=lambda: False,
        )

    assert session is None
    built.assert_not_called()  # nothing touched disk
    warn.assert_not_called()
    body = parent.status_label.text()
    assert "Indexed resources are in use" in body
    assert all(task in body for task in ("mining", "startup prewarm", "card backfill"))


def test_release_true_proceeds_to_the_worker(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    download_dir = tmp_path / "download"
    download_dir.mkdir()
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda **_kwargs: str(download_dir))
    monkeypatch.setattr(mod, "ResourceDownloadWorker", lambda *a, **kw: worker)

    session = start_resource_download(
        parent,
        create_default_config(),
        activate=lambda _s: None,
        release_resources=lambda: True,
    )

    assert isinstance(session, ResourceDownloadSession)
    assert worker.started_event.wait(2.0)
    _drain(qtbot, worker)


# ---------------------------------------------------------------------------
# D15: background, not modal
# ---------------------------------------------------------------------------


def test_module_no_longer_owns_a_nested_event_loop():
    assert not hasattr(mod, "QEventLoop")
    assert not hasattr(mod, "run_resource_download")


def test_window_is_modeless_and_start_returns_while_the_worker_runs(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)

    running_at_return = still_running(worker)
    modality = session.window.windowModality()

    _drain(qtbot, worker)

    assert running_at_return
    assert modality == Qt.WindowModality.NonModal


def test_window_declines_wa_quit_on_close(parent, monkeypatch, tmp_path, qtbot):
    """H3: a still-visible download window must not keep a closed app alive."""
    worker = _FakeWorker(_successful_summary())
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)

    assert session.window.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose) is False

    _drain(qtbot, worker)


def test_hide_leaves_the_worker_and_the_staged_files_alive(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    session, download_dir = _start(monkeypatch, tmp_path, parent, worker)
    assert worker.started_event.wait(2.0)

    session.window.hide_button.click()

    assert not session.window.isVisible()
    assert still_running(worker)
    assert worker.cancel_calls == 0
    assert (download_dir / "in-flight.part").exists()

    _drain(qtbot, worker)


def test_closing_a_live_run_hides_it_rather_than_cancelling(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)
    assert worker.started_event.wait(2.0)

    session.window.close()

    assert not session.window.isVisible()
    assert worker.cancel_calls == 0
    assert still_running(worker)

    _drain(qtbot, worker)


def test_reveal_brings_a_hidden_run_back(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)
    session.window.hide()

    session.reveal()

    assert session.window.isVisible()
    _drain(qtbot, worker)


# ---------------------------------------------------------------------------
# Lifetime (ported): staging survives until the native finish
# ---------------------------------------------------------------------------


def test_staging_dir_is_removed_only_after_the_native_thread_finish(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    session, download_dir = _start(monkeypatch, tmp_path, parent, worker)

    assert worker.summary_emitted.wait(2.0)
    qtbot.wait(20)
    assert download_dir.exists(), "the summary is not the finish barrier"

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: not download_dir.exists(), timeout=3000)
    assert session is not None


def test_terminal_handling_waits_for_the_native_finish(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    outcomes: list = []
    session, _dir = _start(monkeypatch, tmp_path, parent, worker, activate=lambda _s: create_default_config())
    session.finished.connect(outcomes.append)

    assert worker.summary_emitted.wait(2.0)
    qtbot.wait(20)
    assert outcomes == []

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: len(outcomes) == 1, timeout=3000)


def test_worker_is_adopted_so_shutdown_can_join_it(parent, monkeypatch, tmp_path, qtbot):
    adopted: list = []
    worker = _FakeWorker(_successful_summary())
    _session, _dir = _start(monkeypatch, tmp_path, parent, worker, adopt=adopted.append)

    assert adopted == [worker]
    _drain(qtbot, worker)


@pytest.mark.parametrize(
    ("summary", "cancel"),
    [
        pytest.param(_successful_summary(), False, id="success"),
        pytest.param(
            ResourceDownloadSummary(
                results=[ResourceDownloadResult("f", "freq", "Freq", "u", False, "network failed")]
            ),
            False,
            id="failure",
        ),
        pytest.param(ResourceDownloadSummary(cancelled=True, requested_count=1), True, id="cancel"),
    ],
)
def test_mutation_lease_is_released_only_after_native_finish(
    parent,
    monkeypatch,
    tmp_path,
    qtbot,
    summary,
    cancel,
):
    events: list[str] = []
    worker = _FakeWorker(summary)

    def acquire():
        events.append("acquire")
        return create_default_config(), lambda: events.append("release")

    session, _dir = _start(monkeypatch, tmp_path, parent, worker, acquire=acquire)
    assert worker.summary_emitted.wait(2.0)
    qtbot.wait(20)
    assert events == ["acquire"]

    if cancel:
        session.cancel()
    _drain(qtbot, worker)

    assert events == ["acquire", "release"]


def test_mutation_lease_is_released_when_initial_resource_release_raises(parent):
    events: list[str] = []

    def acquire():
        events.append("acquire")
        return create_default_config(), lambda: events.append("release")

    def release_resources() -> bool:
        events.append("resource-release")
        raise RuntimeError("release exploded")

    session = ResourceDownloadSession(
        parent,
        create_default_config(),
        activate=lambda _summary: None,
        release_resources=release_resources,
        acquire_mutation=acquire,
    )

    with pytest.raises(RuntimeError, match="release exploded"):
        session.start()

    assert events == ["acquire", "resource-release", "release"]


def test_mutation_lease_is_released_before_a_raising_blocked_reporter(parent):
    events: list[str] = []

    def acquire():
        events.append("acquire")
        return create_default_config(), lambda: events.append("release")

    def release_resources() -> bool:
        events.append("resource-release")
        return False

    def report_blocked(_message: str) -> None:
        events.append("report")
        raise RuntimeError("reporter exploded")

    session = ResourceDownloadSession(
        parent,
        create_default_config(),
        activate=lambda _summary: None,
        release_resources=release_resources,
        acquire_mutation=acquire,
        blocked=report_blocked,
    )

    with pytest.raises(RuntimeError, match="reporter exploded"):
        session.start()

    assert events == ["acquire", "resource-release", "release", "report"]


def test_promotion_rechecks_release_on_gui_thread_and_preserves_slot(parent, monkeypatch, tmp_path, qtbot):
    from anki_miner.gui.workers import resource_download_worker

    spec = ResourceSpec(
        id="jitendex",
        kind="dict",
        display_name="Jitendex",
        url="https://example.invalid/jitendex.zip",
        license_note="note",
    )
    dicts_root = tmp_path / "dicts"
    slot = dicts_root / spec.id
    slot.mkdir(parents=True)
    marker = slot / "marker"
    marker.write_text("old", encoding="utf-8")
    config = replace(
        create_default_config(),
        dicts_root=dicts_root,
        freqs_root=tmp_path / "freqs",
        pitch_root=tmp_path / "pitch",
    )
    release_threads: list[QThread] = []
    import_calls: list[Path] = []

    def release_resources() -> bool:
        release_threads.append(QThread.currentThread())
        return len(release_threads) == 1

    def fake_download(url, *, dest_dir, **_kwargs):
        downloaded = Path(dest_dir) / "jitendex.zip"
        downloaded.write_bytes(b"ZIP")
        return downloaded

    def fake_import(_source, _root, **_kwargs):
        import_calls.append(marker)
        marker.write_text("new", encoding="utf-8")
        return SimpleNamespace(dict_id=spec.id, source_name="Jitendex", entry_count=1)

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", fake_import)
    monkeypatch.setattr(resource_download_worker, "sweep_superseded_dicts", lambda *_a, **_kw: ([], []))
    adopted = []
    outcomes = []
    session = ResourceDownloadSession(
        parent,
        config,
        activate=lambda _summary: config,
        release_resources=release_resources,
        adopt_worker=adopted.append,
        specs=[spec],
    )
    session.finished.connect(outcomes.append)

    with qtbot.waitSignal(session.finished, timeout=3000):
        assert session.start()
    assert adopted[0].wait(3000)

    assert len(release_threads) == 2
    assert all(thread is parent.thread() for thread in release_threads)
    assert import_calls == []
    assert marker.read_text(encoding="utf-8") == "old"
    assert outcomes[0].activated is False


# ---------------------------------------------------------------------------
# Cancel (ported): one verb, no prompt, cancelled summary preserved
# ---------------------------------------------------------------------------


def test_cancel_locks_the_button_and_asks_the_worker_exactly_once(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)
    assert worker.started_event.wait(2.0)

    with patch("PyQt6.QtWidgets.QMessageBox.question") as question:
        session.window.cancel_button.click()
        session.window.cancel_button.click()  # A second press must change nothing.

    question.assert_not_called()  # D22: Cancel takes no confirmation prompt.
    assert worker.cancel_calls == 1
    assert not session.window.cancel_button.isEnabled()
    assert "Cancelling" in session.window.cancel_button.text()

    _drain(qtbot, worker)


def test_late_progress_cannot_unlock_a_cancelled_run(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)
    session.cancel()

    session._on_item_progress(_progress(_DL, downloaded=1, total_bytes=2))

    assert not session.window.cancel_button.isEnabled()
    assert "Cancelling" in session.window.cancel_button.text()
    _drain(qtbot, worker)


def test_cancel_returns_the_cancelled_summary_with_its_unprocessed_count(parent, monkeypatch, tmp_path, qtbot):
    summary = ResourceDownloadSummary(results=[], cancelled=True, requested_count=3)
    worker = _FakeWorker(summary)
    outcomes: list = []
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)
    session.finished.connect(outcomes.append)
    session.cancel()

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: len(outcomes) == 1, timeout=3000)

    outcome = outcomes[0]
    assert outcome.summary.cancelled is True
    assert outcome.summary.completed_count == 0
    assert outcome.summary.not_processed_count == 3
    assert outcome.activated is False


def test_cancel_after_a_success_keeps_the_completed_work(parent, monkeypatch, tmp_path, qtbot):
    summary = _successful_summary()
    summary.requested_count = 3
    worker = _FakeWorker(summary)
    outcomes: list = []
    session, _dir = _start(monkeypatch, tmp_path, parent, worker, activate=lambda _s: create_default_config())
    session.finished.connect(outcomes.append)
    session.cancel()

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: len(outcomes) == 1, timeout=3000)

    outcome = outcomes[0]
    assert outcome.summary.cancelled is True
    assert outcome.summary.completed_count == 1
    assert outcome.summary.not_processed_count == 2
    assert outcome.activated is True


def test_native_finish_without_a_summary_reports_failure(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(None)
    outcomes: list = []
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)
    session.finished.connect(outcomes.append)

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: len(outcomes) == 1, timeout=3000)

    assert outcomes == [None]
    assert "Failed" in session.window.resource_label.text()
    assert "completion result" in session.window.results_label.text().lower()


# ---------------------------------------------------------------------------
# D19: Installed only after activation
# ---------------------------------------------------------------------------


def test_activation_runs_once_after_the_native_finish_and_yields_installed(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    calls: list = []
    new_config = create_default_config()

    def activate(summary):
        calls.append(summary)
        assert not still_running(worker), "activation must not race the worker's own handles"
        return new_config

    outcomes: list = []
    session, _dir = _start(monkeypatch, tmp_path, parent, worker, activate=activate)
    session.finished.connect(outcomes.append)

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: len(outcomes) == 1, timeout=3000)

    assert len(calls) == 1
    assert outcomes[0].activated is True
    assert outcomes[0].config is new_config
    assert session.window.resource_label.text() == "Resources Installed"


def test_activation_refusal_says_imported_but_not_active(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    outcomes: list = []
    session, _dir = _start(monkeypatch, tmp_path, parent, worker, activate=lambda _s: None)
    session.finished.connect(outcomes.append)

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: len(outcomes) == 1, timeout=3000)

    assert outcomes[0].activated is False
    assert session.window.resource_label.text() == "Imported, but not active — Retry setup"
    assert "Installed" not in session.window.resource_label.text()
    assert session.window.retry_button.isVisible()


def test_changed_live_root_refuses_activation(parent, monkeypatch, tmp_path, qtbot):
    from anki_miner.gui.utils.resource_setup import apply_download_summary

    captured_root = tmp_path / "captured-dicts"
    live_root = tmp_path / "live-dicts"
    base = replace(create_default_config(), dicts_root=captured_root)
    live = replace(base, dicts_root=live_root)
    summary = _successful_summary()
    summary.dicts_root = captured_root
    worker = _FakeWorker(summary)
    outcomes: list = []
    download_dir = tmp_path / "download"
    download_dir.mkdir()
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda **_kwargs: str(download_dir))
    monkeypatch.setattr(mod, "ResourceDownloadWorker", lambda *_args, **_kwargs: worker)
    session = ResourceDownloadSession(
        parent,
        base,
        activate=lambda result: apply_download_summary(live, result),
    )
    session.finished.connect(outcomes.append)
    assert session.start()

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: len(outcomes) == 1, timeout=3000)

    assert outcomes[0].activated is False
    assert outcomes[0].config.dicts_root == captured_root
    assert session.window.resource_label.text() == "Imported, but not active — Retry setup"


def test_a_raising_activator_is_a_refusal_not_a_crash(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    outcomes: list = []

    def activate(_summary):
        raise RuntimeError("settings would not commit")

    session, _dir = _start(monkeypatch, tmp_path, parent, worker, activate=activate)
    session.finished.connect(outcomes.append)

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: len(outcomes) == 1, timeout=3000)

    assert outcomes[0].activated is False
    assert session.window.resource_label.text() == "Imported, but not active — Retry setup"


def test_retry_setup_reruns_activation_without_downloading_again(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    attempts: list = []
    new_config = create_default_config()

    def activate(summary):
        attempts.append(summary)
        return new_config if len(attempts) > 1 else None

    outcomes: list = []
    session, _dir = _start(monkeypatch, tmp_path, parent, worker, activate=activate)
    session.finished.connect(outcomes.append)

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: len(outcomes) == 1, timeout=3000)
    assert outcomes[0].activated is False

    session.window.retry_button.click()

    assert len(attempts) == 2
    assert outcomes[-1].activated is True
    assert session.window.resource_label.text() == "Resources Installed"
    assert not session.window.retry_button.isVisible()
    assert worker.isFinished()  # no second run


def test_activation_is_not_attempted_when_nothing_succeeded(parent, monkeypatch, tmp_path, qtbot):
    summary = ResourceDownloadSummary(
        results=[ResourceDownloadResult("f", "freq", "Freq", "u", False, "network failed")],
        requested_count=1,
    )
    worker = _FakeWorker(summary)
    calls: list = []
    outcomes: list = []
    session, _dir = _start(monkeypatch, tmp_path, parent, worker, activate=lambda s: calls.append(s))
    session.finished.connect(outcomes.append)

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: len(outcomes) == 1, timeout=3000)

    assert calls == []
    assert outcomes[0].activated is False
    assert session.window.resource_label.text() == "Resource Download Failed"
    assert not session.window.retry_button.isVisible()


# ---------------------------------------------------------------------------
# The label the owner actually asked for
# ---------------------------------------------------------------------------


def test_primary_label_shows_transfer_telemetry_and_never_a_url(parent, monkeypatch, tmp_path, qtbot):
    total = 600 * 1024 * 1024
    events = [_progress(_DL, downloaded=step * 32 * 1024 * 1024, total_bytes=total) for step in range(6)]
    worker = _FakeWorker(_successful_summary(), events=events)
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)

    assert worker.progress_done.wait(2.0)
    qtbot.waitUntil(lambda: "/" in session.window.detail_label.text(), timeout=3000)
    text = session.window.detail_label.text()

    assert session.window.resource_label.text() == "Jitendex"
    assert "MB /" in text
    assert "Elapsed" in text
    assert "http" not in text
    assert "http" not in session.window.resource_label.text()

    _drain(qtbot, worker)


def test_install_and_index_phases_replace_the_transfer_line(parent, monkeypatch, tmp_path, qtbot):
    events = [
        _progress(_DL, downloaded=629_145_600, total_bytes=629_145_600),
        _progress(_INSTALL, downloaded=629_145_600, total_bytes=629_145_600),
        _progress(_INDEX, downloaded=629_145_600, total_bytes=629_145_600, entries=184_200),
    ]
    worker = _FakeWorker(_successful_summary(), events=events)
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)

    assert worker.progress_done.wait(2.0)
    qtbot.waitUntil(lambda: "index" in session.window.detail_label.text(), timeout=3000)

    # The live dialog formats through QLocale() -- the *system* locale -- so the
    # separator is the runner's, not en-US's (Qt's C locale sets
    # OmitGroupSeparator). Number formatting is pinned locale-explicitly by
    # test_indexing_detail_states_the_real_entry_count; what this test owns is
    # that the index phase replaces the transfer line at all.
    entries = QLocale().toString(184_200)
    assert session.window.detail_label.text() == f"Building index · {entries} entries"
    _drain(qtbot, worker)


def test_sources_area_carries_the_host_and_licence_not_the_label(parent, monkeypatch, tmp_path, qtbot):
    worker = _FakeWorker(_successful_summary())
    session, _dir = _start(monkeypatch, tmp_path, parent, worker)

    sources = session.window.sources_label.text()

    assert "github.com" in sources
    assert "CC BY-SA 4.0" in sources
    assert "https://" not in sources  # host, not the full asset path
    _drain(qtbot, worker)


# ---------------------------------------------------------------------------
# The task strip: the run stays visible while the window is hidden
# ---------------------------------------------------------------------------


def test_registry_carries_the_run_and_its_transfer_line(parent, monkeypatch, tmp_path, qtbot):
    registry = TaskRegistry()
    total = 600 * 1024 * 1024
    events = [_progress(_DL, downloaded=step * 32 * 1024 * 1024, total_bytes=total) for step in range(6)]
    worker = _FakeWorker(_successful_summary(), events=events)
    session, _dir = _start(monkeypatch, tmp_path, parent, worker, registry=registry)

    assert worker.progress_done.wait(2.0)
    qtbot.waitUntil(lambda: registry.snapshot(mod.TASK_ID).current > 0, timeout=3000)
    snapshot = registry.snapshot(mod.TASK_ID)

    assert snapshot.is_running
    assert snapshot.title == "Recommended resources"
    assert snapshot.owner.main_tab == "settings"
    assert snapshot.total == total
    assert "MB /" in snapshot.detail
    assert session.window is not None

    _drain(qtbot, worker)
    registry.shutdown()


def test_running_registry_rejects_second_start_and_reveals_retained_session(parent, monkeypatch, qtbot):
    registry = TaskRegistry()
    workers: list[_FakeWorker] = []

    def build_worker(*_args, **_kwargs):
        worker = _FakeWorker(_successful_summary())
        workers.append(worker)
        return worker

    monkeypatch.setattr(mod, "ResourceDownloadWorker", build_worker)
    first = start_resource_download(
        parent,
        create_default_config(),
        activate=lambda _summary: None,
        task_registry=registry,
    )
    assert first is not None
    assert workers[0].started_event.wait(2.0)
    first.window.hide()
    first_token = registry.snapshot(mod.TASK_ID).run_token
    registry.reveal_requested.connect(lambda _task_id: first.reveal())

    try:
        second = start_resource_download(
            parent,
            create_default_config(),
            activate=lambda _summary: None,
            task_registry=registry,
        )
        qtbot.wait(20)

        assert second is None
        assert len(workers) == 1
        assert first.window.isVisible()
        assert registry.snapshot(mod.TASK_ID).run_token == first_token
    finally:
        for worker in workers:
            worker.release_native.set()
            assert worker.wait(3000)
        qtbot.wait(20)
        registry.shutdown()


def test_registry_task_ends_cancelled_when_the_run_was_cancelled(parent, monkeypatch, tmp_path, qtbot):
    registry = TaskRegistry()
    worker = _FakeWorker(ResourceDownloadSummary(cancelled=True, requested_count=3))
    session, _dir = _start(monkeypatch, tmp_path, parent, worker, registry=registry)
    session.cancel()

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: not registry.snapshot(mod.TASK_ID).is_running, timeout=3000)

    assert registry.snapshot(mod.TASK_ID).outcome is TaskOutcome.CANCELLED
    registry.shutdown()


def test_registry_task_ends_succeeded_only_when_activation_succeeded(parent, monkeypatch, tmp_path, qtbot):
    registry = TaskRegistry()
    worker = _FakeWorker(_successful_summary())
    session, _dir = _start(monkeypatch, tmp_path, parent, worker, registry=registry, activate=lambda _s: None)
    session.finished.connect(lambda _o: None)

    _drain(qtbot, worker)
    qtbot.waitUntil(lambda: not registry.snapshot(mod.TASK_ID).is_running, timeout=3000)

    assert registry.snapshot(mod.TASK_ID).outcome is TaskOutcome.FAILED
    assert session is not None
    registry.shutdown()


def test_registry_tick_withdraws_a_rate_that_is_no_longer_moving(parent, monkeypatch, tmp_path, qtbot):
    registry = TaskRegistry()
    total = 600 * 1024 * 1024
    now = {"t": 0.0}
    worker = _FakeWorker(_successful_summary())
    session, _dir = _start(monkeypatch, tmp_path, parent, worker, registry=registry, clock=lambda: now["t"])

    for step in range(6):
        now["t"] = float(step) * 4.0
        session._on_item_progress(_progress(_DL, downloaded=step * 32 * 1024 * 1024, total_bytes=total))
    assert "/s" in session.window.detail_label.text()

    # No new bytes. The registry's own tick is what keeps the readout honest;
    # the session must not start a second one-second timer to do it.
    now["t"] += 6.0
    registry.snapshot_changed.emit(mod.TASK_ID)

    text = session.window.detail_label.text()
    assert "/s" not in text
    assert "left" not in text
    assert "No update for 6 s" in text

    _drain(qtbot, worker)
    registry.shutdown()


# ---------------------------------------------------------------------------
# Phase copy (D19)
# ---------------------------------------------------------------------------


def _us_locale() -> QLocale:
    return QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)


def test_download_detail_is_the_owners_transfer_line():
    from anki_miner.gui.utils.progress_telemetry import TransferEstimator

    estimator = TransferEstimator()
    total = 600 * 1024 * 1024
    estimator.update(downloaded=0, total=total, now=0.0)
    for step in range(1, 6):
        stats = estimator.update(downloaded=step * 32 * 1024 * 1024, total=total, now=float(step) * 8.0)

    text = mod.resource_detail(
        _progress(_DL, downloaded=stats.downloaded, total_bytes=total),
        locale=_us_locale(),
        stats=stats,
    )

    assert "160.0 MB / 600.0 MB" in text
    assert "MB/s" in text
    assert "Elapsed" in text
    assert "left" in text


def test_download_detail_without_a_sample_promises_nothing():
    assert mod.resource_detail(_progress(_DL), locale=QLocale()) == "Starting download…"


def test_install_detail_keeps_the_transferred_size():
    text = mod.resource_detail(_progress(_INSTALL, downloaded=600 * 1024 * 1024), locale=_us_locale())
    assert text == "600.0 MB downloaded · Verifying and installing…"


def test_install_detail_omits_a_size_it_does_not_have():
    assert mod.resource_detail(_progress(_INSTALL), locale=QLocale()) == "Verifying and installing…"


def test_indexing_detail_states_the_real_entry_count():
    text = mod.resource_detail(_progress(_INDEX, entries=184_200), locale=_us_locale())
    assert text == "Building index · 184,200 entries"


def test_activating_detail_is_a_phase_not_a_claim_of_success():
    text = mod.resource_detail(_progress(ResourcePhase.ACTIVATING), locale=QLocale())
    assert text == "Activating"
    assert "Installed" not in text


def test_no_detail_ever_carries_the_download_url():
    locale = QLocale()
    for phase in ResourcePhase:
        text = mod.resource_detail(_progress(phase, downloaded=10, entries=5), locale=locale)
        assert "http" not in text


# ---------------------------------------------------------------------------
# Terminal copy (ported from the old results dialog)
# ---------------------------------------------------------------------------


def test_cancel_before_first_item_wording_does_not_imply_installation():
    summary = ResourceDownloadSummary(cancelled=True, requested_count=3)

    headline = result_headline(summary, activated=False)
    body = "\n".join(result_lines(summary))

    assert headline == "Resource Download Cancelled"
    assert "No resources were installed" in body
    assert "Resource items not processed: 3" in body
    assert "Resources Installed" not in headline


def test_cancelled_partial_wording_reports_prior_install_and_unprocessed_count():
    summary = _successful_summary()
    summary.cancelled = True
    summary.requested_count = 3

    headline = result_headline(summary, activated=True)
    body = "\n".join(result_lines(summary))

    assert headline == "Resource Download Cancelled (Some Resources Installed)"
    assert "Some resources were installed before cancellation" in body
    assert "Resource items not processed: 2" in body


def test_results_list_replaced_copy():
    result = ResourceDownloadResult(
        "jitendex",
        "dict",
        "Jitendex",
        "u",
        ok=True,
        detail="100 entries",
        dict_id="jitendex",
        removed_dicts=[("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")],
    )
    body = "\n".join(result_lines(ResourceDownloadSummary(results=[result])))
    assert "Replaced older copy" in body
    assert "Jitendex.org [2025-11-05]" in body


def test_results_surface_failed_removal():
    result = ResourceDownloadResult(
        "jitendex",
        "dict",
        "Jitendex",
        "u",
        ok=True,
        detail="100 entries",
        dict_id="jitendex",
        failed_removals=[("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")],
    )
    body = "\n".join(result_lines(ResourceDownloadSummary(results=[result])))
    assert "Could not remove older copy" in body
    assert "Jitendex.org [2025-11-05]" in body


def test_outcome_reports_activation_separately_from_import():
    summary = _successful_summary()
    outcome = ResourceDownloadOutcome(config=create_default_config(), summary=summary, activated=False)
    assert outcome.activated is False
    assert result_headline(outcome.summary, activated=outcome.activated).startswith("Imported, but not active")


def test_start_resource_download_forwards_a_spec_subset(qtbot, monkeypatch):
    """A page-level picker is worthless if the entry point drops the choice."""
    from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET

    parent = QWidget()
    qtbot.addWidget(parent)
    only_pitch = [s for s in RECOMMENDED_DEFAULT_SET if s.kind == "pitch"]
    seen: list[object] = []

    def fake_start(self):
        seen.append(list(self._specs))
        return True

    monkeypatch.setattr(ResourceDownloadSession, "start", fake_start)
    session = start_resource_download(
        parent,
        create_default_config(),
        activate=lambda _s: None,
        specs=only_pitch,
    )

    assert session is not None
    assert seen == [only_pitch]


def test_start_resource_download_defaults_to_the_whole_catalog(qtbot, monkeypatch):
    """Tools -> Download Recommended Resources passes no specs and must not narrow."""
    from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET

    parent = QWidget()
    qtbot.addWidget(parent)
    seen: list[object] = []

    def fake_start(self):
        seen.append(list(self._specs))
        return True

    monkeypatch.setattr(ResourceDownloadSession, "start", fake_start)
    start_resource_download(parent, create_default_config(), activate=lambda _s: None)

    assert seen == [list(RECOMMENDED_DEFAULT_SET)]

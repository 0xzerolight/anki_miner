"""Tests for the shared reading worker/processor lifecycle base.

``_ReadingMiningTabBase`` owns the run lifecycle that both reading sub-tabs
(manga, novels) share: constructing/starting a :class:`ReadingQueueWorker` over
a caller-supplied READY-items list, the deferred-processor factory path, the
convergent ``_on_worker_finished`` cleanup, ``update_config`` lazy-drop/dirty
semantics, ``release_dictionary_resources``, and ``shutdown``.

These suites are ported from ``test_reading_tab.py`` (TestDeferredProcessor /
TestUpdateConfig / TestReleaseDictionaryResources / TestShutdown, plus the D8
``_build_curation_context`` guard) and re-pointed at the base via a minimal
concrete subclass fixture that supplies only the subclass contract: a review
checkbox, a log widget, the four worker-signal slots, and the
``_after_run_cleanup`` hook.

``ReadingQueueWorker`` is class-level patched at the base module so ``start()``
never spawns a real QThread and constructor kwargs can be inspected — the single
patch target the whole lifecycle funnels through.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QCheckBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets._reading_mining_base import _ReadingMiningTabBase
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.models.reading_queue import ReadingQueueItem
from anki_miner.services.reading.models import ReadingSourceRef

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_CREATE_TARGET = "anki_miner.gui.widgets._reading_mining_base.create_episode_processor"


class _ConcreteReadingTab(_ReadingMiningTabBase):
    """Minimal concrete sub-tab satisfying the base's subclass contract.

    Supplies the checkbox + log widget the base reads, stub worker-signal slots
    (dereferenced at ``.connect()`` time), and an ``_after_run_cleanup`` hook
    that records its invocation count.
    """

    def __init__(self, config, processor=None, presenter=None, parent=None, stats_service=None) -> None:
        super().__init__(config, processor, presenter, parent, stats_service)
        self.review_words_checkbox = QCheckBox()
        self.log_widget = LogWidget()
        self.cleanup_calls = 0

    def _on_item_started(self, idx: int) -> None:  # pragma: no cover - stub slot
        pass

    def _on_item_progress(self, idx: int, label: str, pct: int) -> None:  # pragma: no cover - stub slot
        pass

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:  # pragma: no cover
        pass

    def _on_queue_finished(self) -> None:  # pragma: no cover - stub slot
        pass

    def _after_run_cleanup(self) -> None:
        self.cleanup_calls += 1


def _make_item(kind: str = "mokuro", title: str = "Series Vol.1") -> ReadingQueueItem:
    """Build a READY queue item for a single volume/book."""
    ext = {"mokuro": ".mokuro", "epub": ".epub", "txt": ".txt"}[kind]
    ref = ReadingSourceRef(
        kind=kind,  # type: ignore[arg-type]
        path=Path(f"/src/{title}{ext}"),
        image_root=None,
        title=title,
        volume="1" if kind == "mokuro" else None,
    )
    return ReadingQueueItem(source=ref, title=title, kind=kind)


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig):
    """Instantiate a concrete reading sub-tab with a patched queue worker class.

    ``ReadingQueueWorker`` is patched at the base module so ``start()`` doesn't
    spawn a real QThread; the patch stays active for the whole test body so
    ``_launch_run`` calls inside tests use the mock.
    """
    with patch(_WORKER_TARGET, autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")

        widget = _ConcreteReadingTab(
            config=test_config,
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)
        widget._queue_worker_cls = queue_cls  # type: ignore[attr-defined]
        try:
            yield widget
        finally:
            widget.deleteLater()


class TestLaunchRunGuards:
    """`_launch_run` refuses (returns False) on busy/empty/no-presenter."""

    def test_refused_when_busy(self, tab):
        tab.worker_thread = MagicMock(name="ActiveWorker")
        assert tab._launch_run([_make_item()], preview_mode=False) is False
        tab._queue_worker_cls.assert_not_called()

    def test_refused_when_empty(self, tab):
        assert tab._launch_run([], preview_mode=False) is False
        assert tab.worker_thread is None
        tab._queue_worker_cls.assert_not_called()

    def test_refused_when_presenter_none(self, qtbot, test_config):
        with patch(_WORKER_TARGET, autospec=False) as q_cls:
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            widget = _ConcreteReadingTab(config=test_config, processor=None, presenter=None)
            qtbot.addWidget(widget)
            try:
                assert widget._launch_run([_make_item()], preview_mode=False) is False
                q_cls.assert_not_called()
                assert widget.worker_thread is None
                assert "not initialized" in widget.log_widget.text_edit.toPlainText().lower()
            finally:
                widget.deleteLater()


class TestLaunchRunStart:
    """`_launch_run` constructs, wires, and starts the worker on the happy path."""

    def test_returns_true_and_starts(self, tab):
        item = _make_item()
        assert tab._launch_run([item], preview_mode=False) is True
        assert tab.worker_thread is not None
        tab.worker_thread.start.assert_called_once()
        assert tab._run_items == [item]

    def test_preview_mode_true(self, tab):
        tab._launch_run([_make_item()], preview_mode=True)
        assert tab._queue_worker_cls.call_args.kwargs["preview_mode"] is True

    def test_preview_mode_false(self, tab):
        tab._launch_run([_make_item()], preview_mode=False)
        assert tab._queue_worker_cls.call_args.kwargs["preview_mode"] is False

    def test_passes_config_and_items(self, tab):
        item = _make_item()
        tab._launch_run([item], preview_mode=False)
        kwargs = tab._queue_worker_cls.call_args.kwargs
        assert kwargs["config"] is tab._config
        assert kwargs["items"] == [item]

    def test_wires_worker_signals_to_slots(self, tab):
        tab._launch_run([_make_item()], preview_mode=False)
        worker = tab.worker_thread

        worker.item_started.connect.assert_called_once_with(tab._on_item_started)
        worker.item_progress.connect.assert_called_once_with(tab._on_item_progress)
        worker.item_finished.connect.assert_called_once_with(tab._on_item_finished)
        worker.queue_finished.connect.assert_called_once_with(tab._on_queue_finished)
        worker.finished.connect.assert_called_once_with(tab._on_worker_finished)
        worker.error.connect.assert_called_once_with(tab.log_widget.append_error)

    def test_logs_run_banner(self, tab):
        tab._launch_run([_make_item(), _make_item(title="v2")], preview_mode=False)
        assert "2 items" in tab.log_widget.text_edit.toPlainText()

    def test_curation_callback_none_when_unchecked(self, tab):
        tab._launch_run([_make_item()], preview_mode=False)
        assert tab._queue_worker_cls.call_args.kwargs["curation_callback"] is None

    def test_curation_callback_bridge_when_checked(self, tab):
        tab.review_words_checkbox.setChecked(True)
        tab._launch_run([_make_item()], preview_mode=False)
        # Bound methods compare by ``==`` (fresh wrapper per attribute access).
        assert tab._queue_worker_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge

    def test_does_not_recompute_or_reset_progress(self, tab):
        """Base start path leaves UI (progress/buttons) to the caller — proven by
        the absence of any such attribute on the minimal subclass; the run still
        starts without one."""
        assert not hasattr(tab, "progress_widget")
        assert tab._launch_run([_make_item()], preview_mode=False) is True


class TestItemAt:
    """`_item_at` maps a worker idx against the frozen run snapshot."""

    def test_in_range_returns_item(self, tab):
        item = _make_item()
        tab._launch_run([item], preview_mode=False)
        assert tab._item_at(0) is item

    def test_out_of_range_returns_none(self, tab):
        tab._launch_run([_make_item()], preview_mode=False)
        assert tab._item_at(99) is None
        assert tab._item_at(-1) is None

    def test_none_when_no_run(self, tab):
        assert tab._item_at(0) is None


class TestDeferredProcessor:
    """Tab accepts ``processor=None`` and rebuilds lazily via service_factory."""

    def test_constructs_with_none_processor(self, qtbot, test_config: AnkiMinerConfig):
        sentinel = MagicMock(name="StatsService")
        with patch(_WORKER_TARGET, autospec=False):
            widget = _ConcreteReadingTab(
                config=test_config,
                processor=None,
                presenter=MagicMock(name="Presenter"),
                stats_service=sentinel,
            )
            qtbot.addWidget(widget)
            try:
                assert widget._processor is None
                assert widget._stats_service is sentinel
            finally:
                widget.deleteLater()

    def test_lazy_rebuild_threads_stats_service(self, qtbot, test_config: AnkiMinerConfig):
        """When no processor is cached, the build is deferred to the worker via a
        factory (NOT called on the GUI thread) and the factory threads
        stats_service through ``create_episode_processor``."""
        sentinel_stats = MagicMock(name="StatsService")
        with (
            patch(_WORKER_TARGET, autospec=False) as q_cls,
            patch(_CREATE_TARGET) as mock_create,
        ):
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            built_processor = MagicMock(name="LazyProcessor")
            mock_create.return_value = built_processor

            widget = _ConcreteReadingTab(
                config=test_config,
                processor=None,
                presenter=MagicMock(name="Presenter"),
                stats_service=sentinel_stats,
            )
            qtbot.addWidget(widget)
            try:
                widget._launch_run([_make_item()], preview_mode=False)

                assert mock_create.call_count == 0
                assert q_cls.call_args.kwargs["processor"] is None
                factory = q_cls.call_args.kwargs["processor_factory"]
                assert factory is not None

                assert factory() is built_processor
                assert mock_create.call_count == 1
                assert mock_create.call_args.kwargs["stats_service"] is sentinel_stats
            finally:
                widget.deleteLater()

    def test_cached_processor_passes_prebuilt_no_factory(self, tab):
        tab._launch_run([_make_item()], preview_mode=False)
        assert tab._queue_worker_cls.call_args.kwargs["processor"] is tab._processor
        assert tab._queue_worker_cls.call_args.kwargs["processor_factory"] is None

    def test_worker_finished_caches_built_processor_back(self, qtbot, test_config):
        with patch(_WORKER_TARGET, autospec=False) as q_cls:
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            widget = _ConcreteReadingTab(config=test_config, processor=None, presenter=MagicMock(name="Presenter"))
            qtbot.addWidget(widget)
            try:
                widget._launch_run([_make_item()], preview_mode=False)
                built = MagicMock(name="BuiltProcessor")
                widget.worker_thread.curation_processor = built  # type: ignore[union-attr]

                widget._on_worker_finished()

                assert widget._processor is built
                assert widget.worker_thread is None
            finally:
                widget.deleteLater()


class TestWorkerFinished:
    """`QThread.finished` is the single cleanup signal for every run-exit path."""

    def test_clears_worker_and_snapshot(self, tab):
        tab._launch_run([_make_item()], preview_mode=False)
        assert tab.worker_thread is not None

        tab._on_worker_finished()

        assert tab.worker_thread is None
        assert tab._run_items == []

    def test_calls_after_run_cleanup_hook(self, tab):
        tab._launch_run([_make_item()], preview_mode=False)
        before = tab.cleanup_calls

        tab._on_worker_finished()

        assert tab.cleanup_calls == before + 1


class TestShutdown:
    """shutdown() releases curation, then cancels and joins the worker."""

    def test_shutdown_with_active_worker(self, tab):
        tab._launch_run([_make_item()], preview_mode=False)
        worker = tab.worker_thread

        tab.shutdown()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        worker.wait.assert_called()  # type: ignore[union-attr]
        assert tab.worker_thread is None

    def test_shutdown_releases_curation_before_joining_worker(self, tab):
        with patch.object(tab, "_cancel_active_curation_dialog") as cancel:
            worker = MagicMock(name="QueueWorker")
            order = MagicMock()
            order.attach_mock(cancel, "release")
            order.attach_mock(worker.wait, "wait")
            tab.worker_thread = worker
            tab.shutdown()
            cancel.assert_called_once()
            worker.cancel.assert_called_once()
            called = [c[0] for c in order.mock_calls]
            assert called.index("release") < called.index("wait")

    def test_shutdown_poisons_curation_gate(self, tab):
        tab.worker_thread = MagicMock(name="QueueWorker")
        tab.shutdown()
        assert tab._curation_gate_poisoned is True
        assert tab._curation_event.is_set()

    def test_shutdown_with_nothing_active(self, tab):
        tab.shutdown()  # must not raise

    def test_worker_thread_attr_present(self, tab):
        # BackgroundTaskController duck-types on this public attribute.
        assert hasattr(tab, "worker_thread")


class TestCurationContext:
    """D8: reading curation is table-only — inherit the base (None, None)."""

    def test_build_curation_context_is_none_none(self, tab):
        assert tab._build_curation_context() == (None, None)


class TestUpdateConfig:
    """update_config rebuilds the processor only when idle."""

    def test_update_config_idle_drops_processor_to_none(self, tab, test_config):
        old_processor = tab._processor
        new_cfg = replace(test_config, subtitle_offset=2.5)
        with patch(_CREATE_TARGET) as mock_create:
            tab.update_config(new_cfg)

        assert tab._config is new_cfg
        assert tab._processor is None
        mock_create.assert_not_called()
        old_processor.close.assert_called_once()
        old_processor.release_dictionary_resources.assert_not_called()

    def test_update_config_busy_sets_dirty_flag(self, tab, test_config):
        tab._launch_run([_make_item()], preview_mode=False)
        tab.worker_thread.isRunning.return_value = True  # type: ignore[union-attr]
        original_processor = tab._processor

        new_cfg = replace(test_config, subtitle_offset=2.5)
        with patch(_CREATE_TARGET) as mock_create:
            tab.update_config(new_cfg)

        assert tab._config is new_cfg
        assert tab._processor is original_processor
        assert tab._config_dirty is True
        original_processor.close.assert_not_called()
        mock_create.assert_not_called()

    def test_worker_finished_reconciles_dirty_config(self, tab, test_config):
        tab._launch_run([_make_item()], preview_mode=False)
        tab.worker_thread.isRunning.return_value = True
        original_processor = tab._processor

        new_cfg = replace(test_config, subtitle_offset=2.5)
        tab.update_config(new_cfg)
        assert tab._config_dirty is True

        tab.worker_thread.isRunning.return_value = False
        tab._on_worker_finished()

        original_processor.close.assert_called_once()
        assert tab._processor is None
        assert tab._config_dirty is False


class TestReleaseDictionaryResources:
    """Settings → Remove dictionary drops sqlite handles (Issue #30)."""

    def test_release_when_idle(self, tab):
        processor = tab._processor
        assert tab.release_dictionary_resources() is True
        processor.release_dictionary_resources.assert_called_once()
        assert tab._processor is None

    def test_release_refused_during_run(self, tab):
        tab._launch_run([_make_item()], preview_mode=False)
        tab.worker_thread.isRunning.return_value = True  # type: ignore[union-attr]

        assert tab.release_dictionary_resources() is False
        assert tab._processor is not None

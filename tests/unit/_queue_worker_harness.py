"""Shared test harness for the queue-worker test modules.

Importable factories/classes shared by ``test_youtube_queue_worker.py``,
``test_reading_queue_worker.py`` and ``test_audiobook_queue_worker.py``. These
are deliberately plain helpers, NOT ``conftest.py`` fixtures: the per-domain
fixture signatures differ (youtube needs ``youtube_config``, reading needs
``fake_load``, each mocks a different processor method), so every test module
keeps thin fixture wrappers over these building blocks.

``test_batch_queue_worker.py`` stays separate by design — its worker has a
different arity and does not share this shape.
"""

from __future__ import annotations

import threading
from typing import Any, Callable
from unittest.mock import MagicMock


class SignalCapture:
    """Collect emissions from a Qt signal for later inspection."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args) -> None:
        self.calls.append(args)


def connect_all(worker: Any, *, direct: bool = False) -> dict[str, SignalCapture]:
    """Wire capture objects to all queue worker signals; return them as a dict.

    Args:
        worker: The queue worker whose signals to capture.
        direct: Force ``DirectConnection``. Needed only when ``run()`` is driven
            on a real background thread: the default auto-connection would queue
            every emission onto the main thread's event loop, which a test that
            simply joins the thread never spins.
    """
    captures = {
        "started": SignalCapture(),
        "progress": SignalCapture(),
        "retrying": SignalCapture(),
        "finished": SignalCapture(),
        "paused": SignalCapture(),
        "resumed": SignalCapture(),
        "queue_finished": SignalCapture(),
    }
    signals = {
        "started": worker.item_started,
        "progress": worker.item_progress,
        "retrying": worker.item_retrying,
        "finished": worker.item_finished,
        "paused": worker.run_paused,
        "resumed": worker.run_resumed,
        "queue_finished": worker.queue_finished,
    }
    for key, signal in signals.items():
        if direct:
            from PyQt6.QtCore import Qt

            signal.connect(captures[key], Qt.ConnectionType.DirectConnection)
        else:
            signal.connect(captures[key])
    return captures


def make_mock_processor(method_name: str, return_value: Any) -> MagicMock:
    """MagicMock stand-in for EpisodeProcessor with one method preset.

    ``method_name`` is the mining entrypoint the worker under test calls
    (``process_youtube_url`` / ``process_reading`` / ``process_episode``).
    """
    processor = MagicMock()
    setattr(processor, method_name, MagicMock(return_value=return_value))
    return processor


def make_queue_worker_factory(
    worker_cls: type,
    processor: Any,
    default_config: Any,
    default_item: Callable[[], Any],
) -> Callable[..., Any]:
    """Return a ``_make(items=None, curation_callback=None, config=None)`` factory.

    The returned callable builds ``worker_cls`` with the given prebuilt
    ``processor`` and ``default_config``, defaulting ``items`` to a single
    ``default_item()`` when omitted and allowing a per-call ``config`` override.
    """

    def _make(
        items: list | None = None,
        curation_callback: Any = None,
        config: Any = None,
    ) -> Any:
        if items is None:
            items = [default_item()]
        return worker_cls(
            processor=processor,
            config=config if config is not None else default_config,
            items=items,
            curation_callback=curation_callback,
        )

    return _make


class _PauseAfterFirstWorkerReleaseLock:
    """Pause the worker after its first claim-lock critical section."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._worker_ident: int | None = None
        self._pause_worker = True
        self.worker_paused = threading.Event()
        self.resume_worker = threading.Event()

    def arm_for_current_thread(self) -> None:
        self._worker_ident = threading.get_ident()

    def acquire(self) -> bool:
        return self._lock.acquire()

    def release(self) -> None:
        self._lock.release()
        if self._pause_worker and threading.get_ident() == self._worker_ident:
            self._pause_worker = False
            self.worker_paused.set()
            self.resume_worker.wait()

    def __enter__(self) -> _PauseAfterFirstWorkerReleaseLock:
        self.acquire()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.release()


def race_claim_against_skip(worker: Any, item: Any, on_skipped: Callable[[], None]) -> bool:
    """Run Clear after the worker's first claim-lock critical section.

    An atomic claim records the item before this barrier, so Clear is refused.
    A split-lock claim pauses here between its skip check and claim, so Clear
    removes the row before the worker resumes and exposes the TOCTOU bug.
    """
    errors: list[BaseException] = []
    claim_lock = _PauseAfterFirstWorkerReleaseLock()
    worker._skip_lock = claim_lock

    def _run_worker() -> None:
        claim_lock.arm_for_current_thread()
        try:
            worker.run()
        except BaseException as exc:  # pragma: no cover - re-raised on caller thread
            errors.append(exc)

    worker_thread = threading.Thread(target=_run_worker)
    worker_thread.start()
    try:
        assert claim_lock.worker_paused.wait(1)
        skipped = worker.try_skip_item(item)
        if skipped:
            on_skipped()
    finally:
        claim_lock.resume_worker.set()
        worker_thread.join(3)

    assert not worker_thread.is_alive()
    assert not errors
    return skipped

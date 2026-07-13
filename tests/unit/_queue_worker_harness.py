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

from typing import Any, Callable
from unittest.mock import MagicMock


class SignalCapture:
    """Collect emissions from a Qt signal for later inspection."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args) -> None:
        self.calls.append(args)


def connect_all(worker: Any) -> dict[str, SignalCapture]:
    """Wire capture objects to all queue worker signals; return them as a dict."""
    captures = {
        "started": SignalCapture(),
        "progress": SignalCapture(),
        "finished": SignalCapture(),
        "queue_finished": SignalCapture(),
    }
    worker.item_started.connect(captures["started"])
    worker.item_progress.connect(captures["progress"])
    worker.item_finished.connect(captures["finished"])
    worker.queue_finished.connect(captures["queue_finished"])
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

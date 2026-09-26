"""JSON Lines output for the command line.

stdout carries exactly one thing: one JSON object per line, each with an
``"event"`` key, the last always ``"result"``. Events are written with
``os.write`` to a descriptor, never through ``sys.stdout``: a ``console=False``
frozen Windows build leaves ``sys.stdout`` as ``None`` while fd 1 is still the
pipe the calling tool opened (the contract ``_ffsubsync_child.py`` relies on).
``ensure_ascii`` keeps the stream decodable under any caller locale.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anki_miner.models import ProcessingResult, ValidationResult

logger = logging.getLogger(__name__)

#: Output contract version. Additive fields keep it; a breaking change bumps it.
SCHEMA_VERSION = 1

#: Events that describe the whole run, never one item.
_RUN_EVENTS = frozenset({"start", "result"})


def fd_writer(fd: int) -> Callable[[bytes], None]:
    """A writer that puts every byte on *fd* (``os.write`` may write short)."""

    def write(data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]

    return write


class EventSink:
    """Thread-safe JSON Lines writer (mining stages call in from pool threads)."""

    def __init__(self, write: Callable[[bytes], None] | None = None) -> None:
        self._write = write or fd_writer(1)
        self._lock = threading.Lock()
        self._write_failed = False
        #: Index of the job in flight; stamped onto item-level events that do not name one.
        self.current_item: int | None = None

    def emit(self, event: str, **fields: object) -> None:
        """Write one event line; a closed stream is logged once and otherwise ignored."""
        record: dict[str, object] = {"event": event, **fields}
        if event not in _RUN_EVENTS:
            record.setdefault("item", self.current_item)
        line = json.dumps(record, ensure_ascii=True, default=str) + "\n"
        with self._lock:
            try:
                self._write(line.encode("ascii"))
            except OSError:
                # The caller closed its end; the exit code still reports the run.
                if not self._write_failed:
                    self._write_failed = True
                    logger.warning("CLI event stream closed by the caller; continuing without output")


class EventPresenter:
    """PresenterProtocol → ``message``/``stage`` events. Results go out as ``item_done``."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink

    def _message(self, level: str, message: str) -> None:
        self._sink.emit("message", level=level, text=message)

    def show_info(self, message: str) -> None:
        """Emit an info message."""
        self._message("info", message)

    def show_success(self, message: str) -> None:
        """Emit a success message."""
        self._message("success", message)

    def show_warning(self, message: str) -> None:
        """Emit a warning message."""
        self._message("warning", message)

    def show_error(self, message: str) -> None:
        """Emit an error message."""
        self._message("error", message)

    def show_stage(self, index: int, total: int, name: str) -> None:
        """Emit a pipeline stage."""
        self._sink.emit("stage", index=index, total=total, name=name)

    def show_validation_result(self, result: ValidationResult) -> None:
        """Unused by mining; the runner reports preflight failures itself (no-op)."""
        pass

    def show_processing_result(self, result: ProcessingResult) -> None:
        """The runner emits ``item_done`` from the returned result instead (no-op)."""
        pass

    def show_run_details(self, result: ProcessingResult) -> None:
        """GUI-only: a details dialog the user asked for (no-op)."""
        pass


class EventProgress:
    """ProgressCallback → ``progress`` events. Stages come from the presenter."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink
        self._total = 0

    def on_stage(self, index: int, total: int, name: str) -> None:
        """Ignored: ``EventPresenter.show_stage`` receives the same call (no-op)."""
        pass

    def on_start(self, total: int, description: str) -> None:
        """Emit the start of a counted step."""
        self._total = total
        self._sink.emit("progress", current=0, total=total, desc=description)

    def on_progress(self, current: int, item_description: str) -> None:
        """Emit progress within the current counted step."""
        self._sink.emit("progress", current=current, total=self._total, desc=item_description)

    def on_complete(self) -> None:
        """No event: the next stage or ``item_done`` says it (no-op)."""
        pass

    def on_error(self, item_description: str, error_message: str) -> None:
        """Emit a per-item error as an error message."""
        self._sink.emit("message", level="error", text=f"{item_description}: {error_message}")

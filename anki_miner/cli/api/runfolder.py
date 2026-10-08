"""One run's folder, ``<run_dir>/<run_id>/``: its result files, progress and cancel file."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from anki_miner.utils.atomic_io import atomic_write_path

logger = logging.getLogger(__name__)

PROGRESS = "progress.json"
CANCEL = "cancel"
MEDIA = "media"


def write_json(path: Path, data: object) -> None:
    """UTF-8 without a BOM, replaced atomically."""
    with atomic_write_path(path) as tmp:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def next_numbered(folder: Path, stem: str) -> Path:
    """The next ``<stem>-<n>.json`` in *folder*; ``n`` counts up from 1 per stem.

    A ``<stem>-<n>/`` folder counts too: a render or media call that failed
    midway leaves its files folder without a json, and the next call must not
    reuse that number.
    """
    pattern = re.compile(rf"{re.escape(stem)}-(\d+)(?:\.json)?")
    numbers = [int(m.group(1)) for p in folder.iterdir() if (m := pattern.fullmatch(p.name))]
    return folder / f"{stem}-{max(numbers, default=0) + 1}.json"


def next_result_path(folder: Path) -> Path:
    return next_numbered(folder, "result")


def clear_leftovers(run_dir: Path, run_ids: Iterable[str]) -> None:
    """What an earlier call left in these runs' folders: its ``progress.json`` and an unconsumed ``cancel``.

    Once per call, before its setup: a cancel the caller creates for a queued
    run while the call works still stops that run when it starts (API.md).
    """
    for run_id in run_ids:
        for name in (PROGRESS, CANCEL):
            (run_dir / run_id / name).unlink(missing_ok=True)


class ProgressFile:
    """ProgressCallback -> ``progress.json`` (stage/stages, done/total; no translated names).

    *path* replaces ``<folder>/progress.json`` for a call with no run folder (setup's ``--progress``).
    """

    def __init__(
        self,
        folder: Path,
        run_id: str,
        *,
        path: Path | None = None,
        min_interval: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = path if path is not None else folder / PROGRESS
        self._state: dict[str, object] = {
            "schema": 1,
            "run_id": run_id,
            "stage": 0,
            "stages": 0,
            "done": 0,
            "total": 0,
        }
        self._min_interval = min_interval
        self._clock = clock
        self._last = float("-inf")

    def on_stage(self, index: int, total: int, name: str) -> None:
        self._state.update(stage=index, stages=total, done=0, total=0)
        self._write(force=True)

    def on_start(self, total: int, description: str) -> None:
        self._state.update(done=0, total=total)
        self._write(force=True)

    def on_progress(self, current: int, item_description: str) -> None:
        self._state["done"] = current
        self._write(force=current == self._state["total"])

    def on_complete(self) -> None:
        """No write: the next stage says it."""

    def on_error(self, item_description: str, error_message: str) -> None:
        """No write: errors reach the result file, not the progress file."""

    def _write(self, *, force: bool) -> None:
        now = self._clock()
        if not force and now - self._last < self._min_interval:
            return
        self._last = now
        try:
            write_json(self._path, self._state)
        except OSError:
            logger.warning("API: progress.json not written: %s", self._path, exc_info=True)


class CancelWatcher:
    """Bridge ``<run>/cancel`` (and a SIGINT/SIGTERM) to one run's cancel event.

    Stops that run only. Once the run has stopped on it, the cancel file is
    deleted. A file already present when the run starts (created during the
    call: ``clear_leftovers`` removed an earlier call's) cancels it at once.
    """

    def __init__(self, folder: Path, cancel_all: threading.Event, *, interval: float = 0.2) -> None:
        self._file = folder / CANCEL
        self._cancel_all = cancel_all
        self._interval = interval
        self._stop = threading.Event()
        self.event = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="api-cancel-watch", daemon=True)

    def __enter__(self) -> threading.Event:
        self._thread.start()
        return self.event

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()
        if self.event.is_set():
            self._file.unlink(missing_ok=True)

    def _watch(self) -> None:
        while True:
            if self._cancel_all.is_set() or self._file.exists():
                self.event.set()
                return
            if self._stop.wait(self._interval):
                return

"""One run's folder, ``<run_dir>/<run_id>/``: its files, progress, cancel file and saved run."""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anki_miner import __version__
from anki_miner.cli.api.files import Episode
from anki_miner.utils.atomic_io import atomic_write_path

logger = logging.getLogger(__name__)

CANDIDATES = "candidates.json"
SAVED_RUN = "prepared.json"
PROGRESS = "progress.json"
CANCEL = "cancel"
MEDIA = "media"
_RESULT = re.compile(r"result-(\d+)\.json")


def reset_run_folder(folder: Path) -> None:
    """prepare on an existing run_id replaces it: this API's own files go, anything else stays."""
    folder.mkdir(exist_ok=True)
    for name in (CANDIDATES, SAVED_RUN, PROGRESS, CANCEL):
        (folder / name).unlink(missing_ok=True)
    for path in folder.iterdir():
        if _RESULT.fullmatch(path.name):
            path.unlink(missing_ok=True)
    shutil.rmtree(folder / MEDIA, ignore_errors=True)


def write_json(path: Path, data: object) -> None:
    """UTF-8 without a BOM, replaced atomically."""
    with atomic_write_path(path) as tmp:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> object | None:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data


def next_result_path(folder: Path) -> Path:
    numbers = [int(m.group(1)) for p in folder.iterdir() if (m := _RESULT.fullmatch(p.name))]
    return folder / f"result-{max(numbers, default=0) + 1}.json"


class ProgressFile:
    """ProgressCallback -> ``progress.json`` (stage/stages, done/total; no translated names)."""

    def __init__(
        self,
        folder: Path,
        run_id: str,
        *,
        min_interval: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = folder / PROGRESS
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
    deleted. A file already present when the run starts cancels it at once.
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


def file_stamp(path: Path | None) -> list[int] | None:
    """``[size, mtime_ns]``, or None for no file."""
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return [stat.st_size, stat.st_mtime_ns]


def _json_copy(value: Any) -> Any:
    """*value* as JSON gives it back, so a captured run compares equal to a read one."""
    return json.loads(json.dumps(value))


@dataclass(frozen=True)
class SavedRun:
    """What prepare saw, so commit can refuse a run whose inputs or settings moved (RUN_STALE)."""

    app_version: str
    profile: str | None
    language: object
    overlay: Mapping[str, object]
    episode: Mapping[str, object]
    config: Mapping[str, object]
    inputs: Mapping[str, list[int] | None]
    indexes: list[list[object]]

    @classmethod
    def capture(
        cls,
        *,
        profile: str | None,
        language: object,
        overlay: Mapping[str, object],
        episode: Episode,
        config_view: Mapping[str, object],
        indexes: list[list[object]],
    ) -> SavedRun:
        inputs = {
            "video_file": file_stamp(episode.video_file),
            "subtitle_file": file_stamp(episode.subtitle_file),
            "secondary_subtitle_file": file_stamp(episode.secondary_subtitle_file),
        }
        return cls(
            app_version=__version__,
            profile=profile,
            language=_json_copy(language),
            overlay=_json_copy(dict(overlay)),
            episode=_json_copy(dict(episode.raw)),
            config=_json_copy(dict(config_view)),
            inputs=inputs,
            indexes=_json_copy(indexes),
        )

    def to_json(self) -> dict[str, object]:
        return {
            "schema": 1,
            "app_version": self.app_version,
            "profile": self.profile,
            "language": self.language,
            "overlay": dict(self.overlay),
            "episode": dict(self.episode),
            "config": dict(self.config),
            "inputs": dict(self.inputs),
            "indexes": self.indexes,
        }

    @classmethod
    def from_json(cls, data: object) -> SavedRun | None:
        if not isinstance(data, dict) or data.get("schema") != 1:
            return None
        try:
            return cls(
                app_version=data["app_version"],
                profile=data["profile"],
                language=data["language"],
                overlay=data["overlay"],
                episode=data["episode"],
                config=data["config"],
                inputs=data["inputs"],
                indexes=data["indexes"],
            )
        except KeyError:
            return None

    @classmethod
    def read(cls, folder: Path) -> SavedRun | None:
        return cls.from_json(read_json(folder / SAVED_RUN))

    def stale_reason(self, now: SavedRun) -> str | None:
        """Why *now* no longer matches this saved run, or None."""
        if self.app_version != now.app_version:
            return "Anki Miner was updated since prepare."
        for key, stamp in self.inputs.items():
            if now.inputs.get(key) != stamp:
                return f"{Path(str(self.episode.get(key, key))).name} changed since prepare."
        if self.config != now.config:
            return "Settings changed since prepare."
        if self.indexes != now.indexes:
            return "Dictionaries or frequency lists changed since prepare."
        return None

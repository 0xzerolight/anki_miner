"""Commands that turn files into files (``fetch``, ``ocr``, ``sync``, …): one run per episode (API.md).

A call clears what an earlier call left in its runs' folders, runs its own
checks once, then each episode in turn. A run that ends writes
``<run_id>/<stem>-<n>.json`` and keeps its files in ``<stem>-<n>/`` beside it;
a run that fails removes that folder. Its temp files live in
``<run_id>/media/``, removed when the run ends. Nothing here takes a lock.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TypeVar

from anki_miner.cli.api.contract import CANCELLED, INTERNAL, ApiError, run_verdict
from anki_miner.cli.api.files import E, JobsFile
from anki_miner.cli.api.runfolder import (
    MEDIA,
    CancelWatcher,
    ProgressFile,
    clear_leftovers,
    next_numbered,
    write_json,
)

logger = logging.getLogger(__name__)

C = TypeVar("C")


class RunFailed(ApiError):
    """A run's failure that running again later can get past when ``transient``."""

    def __init__(self, code: str, message: str, *, transient: bool = False) -> None:
        super().__init__(code, message)
        self.transient = transient


@dataclass(frozen=True)
class Run:
    """What one run's body gets: its folders, its cancel event and its progress file."""

    run_id: str
    #: ``<run_dir>/<run_id>/``
    folder: Path
    #: ``<stem>-<n>/`` beside the run's json, not yet created; removed when the run fails
    out: Path
    #: ``<run_id>/media/``, the run's temp folder; removed when the run ends
    media: Path
    cancel: threading.Event
    progress: ProgressFile


def verdict(run_id: str, *, error: ApiError | None = None, file: str | None = None) -> dict[str, object]:
    """One entry of a job call's ``runs``: ``run_verdict`` and ``failure_is_transient``."""
    transient = isinstance(error, RunFailed) and error.transient
    return {**run_verdict(run_id, error=error, file=file), "failure_is_transient": transient}


def job_runs(
    job: JobsFile[E],
    stem: str,
    cancel_all: threading.Event,
    prepare: Callable[[], C],
    body: Callable[[C, E, Run], dict[str, object]],
) -> list[dict[str, object]]:
    """Every episode of *job* in turn; *body*'s record goes to ``<stem>-<n>.json``.

    *prepare* runs the call's own checks once (an ApiError refuses the whole
    call) and returns what every run shares, typically the settings. *body*
    returns the record without ``schema`` and ``run_id``; an ApiError ends its
    run with that code.
    """
    clear_leftovers(job.run_dir, (episode.run_id for episode in job.episodes))
    shared = prepare()
    return [
        _guarded(episode.run_id, partial(_one, job.run_dir, episode, stem, cancel_all, partial(body, shared)))
        for episode in job.episodes
    ]


def _guarded(run_id: str, run: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return run()
    except ApiError as exc:
        return verdict(run_id, error=exc)
    except Exception as exc:  # noqa: BLE001 — one broken run must not end the call
        logger.exception("API run %s failed", run_id)
        return verdict(run_id, error=ApiError(INTERNAL, f"{type(exc).__name__}: {exc}"))


def _one(
    run_dir: Path,
    episode: E,
    stem: str,
    cancel_all: threading.Event,
    body: Callable[[E, Run], dict[str, object]],
) -> dict[str, object]:
    folder = run_dir / episode.run_id
    folder.mkdir(exist_ok=True)
    if cancel_all.is_set():
        raise ApiError(CANCELLED, "Cancelled before this run started.")
    # A crash's leftover: a <stem>-<n>/ that no <stem>-<n>.json names. It goes, and
    # its number with it (next_numbered counts folders).
    workspace = re.compile(rf"{re.escape(stem)}-\d+")
    for leftover in folder.iterdir():
        if workspace.fullmatch(leftover.name) and leftover.is_dir() and not leftover.with_suffix(".json").exists():
            shutil.rmtree(leftover, ignore_errors=True)
    path = next_numbered(folder, stem)
    out = path.with_suffix("")  # <stem>-<n>/ beside <stem>-<n>.json
    try:
        with CancelWatcher(folder, cancel_all) as cancel:
            run = Run(episode.run_id, folder, out, folder / MEDIA, cancel, ProgressFile(folder, episode.run_id))
            record = body(episode, run)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(folder / MEDIA, ignore_errors=True)
    write_json(path, {"schema": 1, "run_id": episode.run_id, **record})
    return verdict(episode.run_id, file=path.name)

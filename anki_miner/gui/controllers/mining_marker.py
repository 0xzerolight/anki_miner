"""The window's mining marker: a lock file held while any mining screen runs.

``--api mine`` runs beside an open window, but not beside one that mines or
shows its Word Curator, which opens only inside a run. The task registry is
where every screen's run starts and ends, so the window holds
``instance.mining-<pid>.lock`` while a mining screen's task is running there.
cli/entry.py acquire_run_lock probes it the way it probes the window marker;
that probe reclaims a crashed window's file.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path

from PyQt6.QtCore import QLockFile, QObject

from anki_miner.gui.controllers.task_registry import TaskRegistry

logger = logging.getLogger(__name__)

MINING_MARKER_PREFIX = "instance.mining-"


def mining_task_ids(screen_types: Iterable[type]) -> frozenset[str]:
    """The task ids the mining screens among *screen_types* publish their runs under.

    A mining screen is a MiningTabBase. Card Backfill and Restyle write to Anki
    as well, but only to notes that exist, and Deck Filter adds only copies of
    existing notes (add_notes_raw); a mine run adds new ones, and AnkiConnect
    serializes them all.
    """
    from anki_miner.gui.widgets._mining_tab_base import MiningTabBase

    return frozenset(t.TASK_ID for t in screen_types if issubclass(t, MiningTabBase) and t.TASK_ID)


class MiningMarker(QObject):
    """Holds this window's mining marker while a mining task runs."""

    def __init__(
        self, registry: TaskRegistry, home: Path, task_ids: Iterable[str], parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._task_ids = frozenset(task_ids)
        self._lock = QLockFile(str(home / f"{MINING_MARKER_PREFIX}{os.getpid()}.lock"))
        self._held = False
        self._refused = False  # one warning per run, not one per registry tick
        registry.snapshot_changed.connect(self._sync)

    @property
    def task_ids(self) -> frozenset[str]:
        return self._task_ids

    @property
    def held(self) -> bool:
        return self._held

    def _sync(self, _task_id: str = "") -> None:
        mining = any(snapshot.task_id in self._task_ids for snapshot in self._registry.running())
        if mining and not self._held:
            self._held = self._lock.tryLock(0)
            if self._held:
                logger.info("Mining marker held: %s", self._lock.fileName())
            elif not self._refused:
                self._refused = True
                logger.warning(
                    "Mining marker could not be taken; --api mine will not see this run: %s", self._lock.fileName()
                )
        elif not mining:
            self._refused = False
            self.release()

    def release(self) -> None:
        """Drop the marker. Safe to call more than once; the app's exit calls it."""
        if self._held:
            self._lock.unlock()
            self._held = False
            logger.info("Mining marker released")

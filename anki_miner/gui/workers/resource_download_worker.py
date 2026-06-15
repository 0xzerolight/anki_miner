"""Background worker that downloads + imports recommended resources.

Given a list of :class:`ResourceSpec`, downloads each artifact to a temp file
then routes it to the right importer based on ``kind`` (``dict`` → Yomitan
dictionary importer, ``freq`` → Yomitan frequency importer, ``pitch`` → raw
drop-in TSV write). Each item is wrapped in its own ``try/except`` so one
failure never aborts the batch; the per-item outcomes are collected into a
:class:`ResourceDownloadSummary` emitted at the end.

This worker NEVER mutates config. The summary is its sole output — a later
task reads ``summary.succeeded`` (plus each result's ``kind`` / ``dict_id``)
to build the config mutations.

Imports the three routing callables as bare module-level names so tests can
``monkeypatch.setattr`` them.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.frequency.yomitan_freq_importer import import_yomitan_freq_zip
from anki_miner.services.resource_downloader import download_to_temp

if TYPE_CHECKING:
    from collections.abc import Sequence

    from anki_miner.services.resource_catalog import ResourceSpec

logger = logging.getLogger(__name__)


@dataclass
class ResourceDownloadResult:
    """Outcome of downloading + importing one recommended resource."""

    spec_id: str
    kind: str
    display_name: str
    url: str
    ok: bool
    detail: str
    dict_id: str | None = None


@dataclass
class ResourceDownloadSummary:
    """Aggregate of all per-item results from a worker run."""

    results: list[ResourceDownloadResult] = field(default_factory=list)

    @property
    def succeeded(self) -> list[ResourceDownloadResult]:
        """Results that imported successfully."""
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[ResourceDownloadResult]:
        """Results that failed at download or import."""
        return [r for r in self.results if not r.ok]


class ResourceDownloadWorker(CancellableWorker):
    """Download + import a batch of recommended resources off the GUI thread."""

    # (spec_id, current, total, message)
    item_progress = pyqtSignal(str, int, int, str)
    # (spec_id, ok, detail)
    item_done = pyqtSignal(str, bool, str)
    # Emits the ResourceDownloadSummary. Named *_summary to avoid colliding
    # with QThread.finished, which the codebase relies on.
    finished_summary = pyqtSignal(object)

    def __init__(
        self,
        specs: Sequence[ResourceSpec],
        *,
        dicts_root: Path,
        frequency_csv: Path,
        pitch_csv: Path,
        download_dir: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._specs = list(specs)
        self._dicts_root = dicts_root
        self._frequency_csv = frequency_csv
        self._pitch_csv = pitch_csv
        self._download_dir = download_dir

    def _progress_for(self, spec_id: str) -> Callable[[int, int, str], None]:
        """Return a (current, total, message) callback that tags emits with spec_id."""

        def emit(current: int, total: int, message: str) -> None:
            self.item_progress.emit(spec_id, current, total, message)

        return emit

    def run(self) -> None:
        """Download + import each spec in order, isolating per-item failures."""
        summary = ResourceDownloadSummary()

        for spec in self._specs:
            if self.check_cancelled():
                break

            temp: Path | None = None
            try:
                temp = download_to_temp(
                    spec.url,
                    dest_dir=self._download_dir,
                    progress=self._progress_for(spec.id),
                    cancelled_check=lambda: self.is_cancelled,
                )

                dict_id: str | None = None
                if spec.kind == "dict":
                    result = import_yomitan_zip(
                        temp,
                        self._dicts_root,
                        overwrite=True,
                        cancel_check=lambda: self.is_cancelled,
                        progress=self._progress_for(spec.id),
                    )
                    dict_id = result.dict_id
                    detail = f"{result.entry_count} entries"
                elif spec.kind == "freq":
                    freq_result = import_yomitan_freq_zip(
                        temp,
                        self._frequency_csv,
                        cancel_check=lambda: self.is_cancelled,
                        progress=self._progress_for(spec.id),
                    )
                    detail = f"{freq_result.entry_count} entries"
                elif spec.kind == "pitch":
                    self._pitch_csv.parent.mkdir(parents=True, exist_ok=True)
                    # shutil.move (not os.replace): download_dir and pitch_csv may
                    # be on different filesystems (download_dir under the system
                    # temp dir / tmpfs), where os.replace raises a cross-device
                    # link error. shutil.move falls back to copy+unlink.
                    shutil.move(str(temp), str(self._pitch_csv))
                    detail = "downloaded"
                else:  # pragma: no cover — catalog kinds are constrained
                    raise ValueError(f"Unknown resource kind: {spec.kind!r}")

                summary.results.append(
                    ResourceDownloadResult(
                        spec_id=spec.id,
                        kind=spec.kind,
                        display_name=spec.display_name,
                        url=spec.url,
                        ok=True,
                        detail=detail,
                        dict_id=dict_id,
                    )
                )
                self.item_done.emit(spec.id, True, detail)
            except Exception as exc:  # noqa: BLE001 — isolate per-item failures
                # Reached only when the route raised: download succeeded but the
                # importer / pitch move failed, so the temp still exists (the
                # downloader cleans up only when IT raises; a successful move or
                # import never reaches here). Best-effort unlink.
                if temp is not None:
                    with contextlib.suppress(OSError):
                        temp.unlink()
                logger.debug("resource %s failed: %s", spec.id, exc)
                summary.results.append(
                    ResourceDownloadResult(
                        spec_id=spec.id,
                        kind=spec.kind,
                        display_name=spec.display_name,
                        url=spec.url,
                        ok=False,
                        detail=str(exc),
                    )
                )
                self.item_done.emit(spec.id, False, str(exc))

        self.finished_summary.emit(summary)

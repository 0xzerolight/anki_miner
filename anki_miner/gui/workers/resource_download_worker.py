"""Background worker that downloads + imports recommended resources.

Given a list of :class:`ResourceSpec`, downloads each artifact to a temp file
then routes it to the right importer based on ``kind`` (``dict`` → Yomitan
dictionary importer, ``freq`` → per-source frequency importer, ``pitch`` → raw
drop-in TSV write). Each item is wrapped in its own ``try/except`` so one
failure never aborts the batch; the per-item outcomes are collected into a
:class:`ResourceDownloadSummary` emitted at the end.

The ``freq`` route is chain-native: it imports into ``freqs_root/<source_id>/``
exactly as the ``dict`` route imports into ``dicts_root/<dict_id>/``, and the
result carries ``source_id`` so the config step can prepend a ``FreqEntry``.

This worker NEVER mutates config. The summary is its sole output — a later
task reads ``summary.succeeded`` (plus each result's ``kind`` / ``dict_id`` /
``source_id``) to build the config mutations.

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
from anki_miner.services.frequency.source_importer import import_frequency_source
from anki_miner.services.resource_downloader import download_to_temp

if TYPE_CHECKING:
    from collections.abc import Sequence

    from anki_miner.services.resource_catalog import ResourceSpec

logger = logging.getLogger(__name__)

# Suffixes import_frequency_source dispatches on; anything else falls back to .zip
# (the recommended freq resources are Yomitan zips).
_FREQ_SUFFIXES = {".zip", ".csv", ".tsv", ".txt"}


def _retype_for_suffix(temp: Path, url: str) -> Path:
    """Rename ``temp`` to carry the URL's recognised suffix; return the new path.

    ``download_to_temp`` always stages a ``.part`` file, but the frequency
    importer dispatches on suffix. Pick the suffix from the catalog URL (default
    ``.zip``) and rename in place; if the rename fails, fall back to the original
    path unchanged.
    """
    suffix = Path(url).suffix.lower()
    if suffix not in _FREQ_SUFFIXES:
        suffix = ".zip"
    retyped = temp.with_name(temp.stem + suffix)
    if retyped == temp:
        return temp
    try:
        temp.rename(retyped)
    except OSError:
        return temp
    return retyped


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
    source_id: str | None = None


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
        freqs_root: Path,
        pitch_csv: Path,
        download_dir: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._specs = list(specs)
        self._dicts_root = dicts_root
        self._freqs_root = freqs_root
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
                source_id: str | None = None
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
                    # import_frequency_source dispatches on file suffix (.zip vs
                    # .csv/.tsv/.txt), but download_to_temp always stages a
                    # ``.part`` file. Re-suffix the temp from the catalog URL so
                    # the importer routes correctly (and copies a sensibly-named
                    # source.<ext> alongside the index).
                    temp = _retype_for_suffix(temp, spec.url)
                    freq_result = import_frequency_source(
                        temp,
                        self._freqs_root,
                        cancel_check=lambda: self.is_cancelled,
                        progress=self._progress_for(spec.id),
                    )
                    source_id = freq_result.source_id
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
                        source_id=source_id,
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
                logger.debug("resource %s failed: %s", spec.id, exc, exc_info=True)
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

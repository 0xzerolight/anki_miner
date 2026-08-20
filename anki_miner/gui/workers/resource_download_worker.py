"""Background worker that downloads + imports recommended resources.

Given a list of :class:`ResourceSpec`, downloads each artifact to a temp file
then routes it to the right importer based on ``kind`` (``dict`` → Yomitan
dictionary importer, ``freq`` → per-source frequency importer, ``pitch`` →
per-source pitch importer). Each item is wrapped in its own ``try/except`` so one
failure never aborts the batch; the per-item outcomes are collected into a
:class:`ResourceDownloadSummary` emitted at the end.

The ``freq`` and ``pitch`` routes are chain-native: they import into
``freqs_root/<source_id>/`` / ``pitch_root/<source_id>/`` exactly as the
``dict`` route imports into ``dicts_root/<dict_id>/``, and the result carries
``source_id`` so the config step can prepend a ``FreqEntry`` /
``PitchSourceEntry``.

This worker NEVER mutates config. The summary is its sole output — a later
task reads ``summary.succeeded`` (plus each result's ``kind`` / ``dict_id`` /
``source_id``) to build the config mutations.

Imports the three routing callables as bare module-level names so tests can
``monkeypatch.setattr`` them.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication, pyqtSignal

from anki_miner.exceptions import SetupError
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.superseded import sweep_superseded_dicts
from anki_miner.services.frequency.source_importer import import_frequency_source
from anki_miner.services.pitch_accent.source_importer import import_pitch_source
from anki_miner.services.resource_downloader import download_to_temp
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.slug import slugify

if TYPE_CHECKING:
    from collections.abc import Sequence

    from anki_miner.services.resource_catalog import ResourceSpec

logger = logging.getLogger(__name__)

# Suffixes the freq/pitch source importers dispatch on; anything else falls
# back to .zip (the recommended zip-shaped resources are Yomitan zips).
_FREQ_SUFFIXES = {".zip", ".csv", ".tsv", ".txt"}


def _resume_key(spec: ResourceSpec) -> str:
    """Stable, collision-free resume key for one catalogue resource (D16-C).

    Slugged rather than used verbatim: the key names a file, and a catalogue id
    is free-form text. Two resources with different ids can never slug to the
    same key within a kind because the ids themselves are the pinned on-disk
    slot names, which already have to be distinct.
    """
    return f"resource-{slugify(spec.kind, fallback='res')}-{slugify(spec.id, fallback='item')}"


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


class ResourcePhase(Enum):
    """Where one resource is in the pipeline.

    Stated by the worker rather than inferred by the view: the phase used to be
    guessed from English progress text, which meant a translated build, a
    reworded importer message or a new importer silently broke the readout.

    ``ACTIVATING`` is never emitted here. Only the config-owning caller can say
    the resource is being switched on, and only it can say when it is genuinely
    installed — which is the whole point of separating import from activation.
    """

    DOWNLOADING = "downloading"
    #: Bytes are in; the archive is being validated and unpacked. Nothing
    #: countable is happening yet.
    INSTALLING = "installing"
    #: Entries are being written and the count is real.
    INDEXING = "indexing"
    ACTIVATING = "activating"


@dataclass(frozen=True)
class ResourceProgress:
    """One observation about one resource. Every number is one the app has.

    ``total_bytes`` and ``entries`` are ``None`` rather than 0 when unknown —
    a server that sends no Content-Length has not told us the download is
    empty, and an importer that counts files has not told us how many entries
    it wrote.
    """

    spec_id: str
    display_name: str
    phase: ResourcePhase
    #: Bytes transferred. Retained across the install phases so the view can
    #: keep saying how large the thing it is now unpacking was.
    downloaded: int = 0
    total_bytes: int | None = None
    entries: int | None = None


class ResourcePromotionRequest:
    """Worker-to-GUI handshake immediately before an importer can promote."""

    def __init__(self) -> None:
        self._resolved = Event()
        self._allowed = False

    def resolve(self, allowed: bool) -> None:
        """Return the GUI thread's resource-release decision to the worker."""
        self._allowed = allowed
        self._resolved.set()

    def wait(self, cancelled: Callable[[], bool]) -> bool:
        """Wait for the GUI decision while remaining responsive to Cancel."""
        while not self._resolved.wait(0.05):
            if cancelled():
                return False
        return self._allowed


class _ItemPhaseReporter:
    """Folds one resource's two progress streams into one phase sequence.

    The download reports bytes; the importer reports either entries (the
    dictionary route, which inserts row by row) or file indices (the frequency
    and pitch routes, which walk bank files). Only the first is an entry count,
    so only the first is allowed to claim one.
    """

    def __init__(self, spec: ResourceSpec, emit: Callable[[ResourceProgress], None], *, counts_entries: bool) -> None:
        self._spec = spec
        self._emit = emit
        self._counts_entries = counts_entries
        self._downloaded = 0
        self._total_bytes: int | None = None
        self._entries: int | None = None

    def downloading(self, downloaded: int, total: int, _message: str) -> None:
        """Record a byte observation from the downloader."""
        self._downloaded = downloaded
        self._total_bytes = total or None
        self._publish(ResourcePhase.DOWNLOADING)

    def installing(self) -> None:
        """Announce the download→install transition before the importer runs."""
        self._publish(ResourcePhase.INSTALLING)

    def importing(self, _current: int, _total: int, message: str) -> None:
        """Record an importer observation, promoting to INDEXING once counting.

        The promotion latches: an importer that finishes inserting and then
        emits an uncounted "Finalizing" step has not stopped indexing, and a
        readout that drops back to a vaguer phase reads as going backwards.

        ``current`` is NOT an entry count for the dict route — since the
        monotonic-progress fix it is a scaled files_done/bank fraction (see
        ``_PROGRESS_SCALE`` in yomitan_importer.py), so latching it here would
        surface a bank-derived number as if it were entries. The real count
        only exists in the message text; both sides of this coupling are
        internal English strings, never translated.
        """
        if self._counts_entries:
            match = re.match(r"Inserted ([\d,]+) entries", message)
            if match:
                self._entries = max(int(match.group(1).replace(",", "")), self._entries or 0)
        self._publish(ResourcePhase.INDEXING if self._entries else ResourcePhase.INSTALLING)

    def _publish(self, phase: ResourcePhase) -> None:
        self._emit(
            ResourceProgress(
                spec_id=self._spec.id,
                display_name=self._spec.display_name,
                phase=phase,
                downloaded=self._downloaded,
                total_bytes=self._total_bytes,
                entries=self._entries,
            )
        )


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
    # Date-versioned duplicate dicts superseded by this import (id, source_name).
    # ``removed_dicts`` were deleted from disk (the config step drops their chain
    # entries); ``failed_removals`` matched but could not be deleted and are
    # surfaced to the user, their chain entries left intact (no orphan).
    removed_dicts: list[tuple[str, str]] = field(default_factory=list)
    failed_removals: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ResourceDownloadSummary:
    """Aggregate of all per-item results from a worker run."""

    results: list[ResourceDownloadResult] = field(default_factory=list)
    cancelled: bool = False
    requested_count: int = 0
    dicts_root: Path | None = None
    freqs_root: Path | None = None
    pitch_root: Path | None = None

    @property
    def succeeded(self) -> list[ResourceDownloadResult]:
        """Results that imported successfully."""
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[ResourceDownloadResult]:
        """Results that failed at download or import."""
        return [r for r in self.results if not r.ok]

    @property
    def completed_count(self) -> int:
        """Number of items that reached a success or failure outcome."""
        return len(self.results)

    @property
    def not_processed_count(self) -> int:
        """Number of requested items left without an outcome."""
        return max(self.requested_count - self.completed_count, 0)


class ResourceDownloadWorker(CancellableWorker):
    """Download + import a batch of recommended resources off the GUI thread."""

    # Emits a ResourceProgress. Typed rather than (id, current, total, message)
    # so the view reads a stated phase instead of matching English text.
    item_progress = pyqtSignal(object)
    # (spec_id, ok, detail)
    item_done = pyqtSignal(str, bool, str)
    # Emits the ResourceDownloadSummary. Named *_summary to avoid colliding
    # with QThread.finished, which the codebase relies on.
    finished_summary = pyqtSignal(object)
    # A blocking worker-side request whose decision is made by the GUI thread.
    # The session connects this before start; direct unit use stays ungated.
    promotion_requested = pyqtSignal(object)

    def __init__(
        self,
        specs: Sequence[ResourceSpec],
        *,
        dicts_root: Path,
        freqs_root: Path,
        pitch_root: Path,
        download_dir: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._specs = list(specs)
        self._dicts_root = dicts_root
        self._freqs_root = freqs_root
        self._pitch_root = pitch_root
        self._download_dir = download_dir
        self._promotion_approval_required = False

    def require_promotion_approval(self) -> None:
        """Require a GUI-thread release handshake before every importer."""
        self._promotion_approval_required = True

    def _promotion_allowed(self) -> bool:
        if not self._promotion_approval_required:
            return True
        request = ResourcePromotionRequest()
        self.promotion_requested.emit(request)
        return request.wait(lambda: self.is_cancelled)

    @staticmethod
    def _promotion_blocked_detail() -> str:
        return QCoreApplication.translate(
            "ResourceDownloadDialog",
            "Indexed resources became busy before installation; existing resources were left unchanged.",
        )

    def _require_promotion_allowed(self) -> None:
        if not self._promotion_allowed():
            raise SetupError(self._promotion_blocked_detail())

    def _reporter_for(self, spec: ResourceSpec) -> _ItemPhaseReporter:
        """Return the phase reporter for one resource."""
        return _ItemPhaseReporter(spec, self.item_progress.emit, counts_entries=spec.kind == "dict")

    def run(self) -> None:
        """Download + import each spec in order, isolating per-item failures."""
        self.log_start("ResourceDownloadWorker", specs=len(self._specs))
        summary = ResourceDownloadSummary(
            requested_count=len(self._specs),
            dicts_root=self._dicts_root,
            freqs_root=self._freqs_root,
            pitch_root=self._pitch_root,
        )

        for spec in self._specs:
            if self.check_cancelled():
                summary.cancelled = True
                break

            temp: Path | None = None
            reporter = self._reporter_for(spec)
            try:
                temp = download_to_temp(
                    spec.url,
                    dest_dir=self._download_dir,
                    progress=reporter.downloading,
                    cancelled_check=lambda: self.is_cancelled,
                    read_timeout_seconds=1.0,
                    # The 580-of-600 MB case D16-C exists for. ``spec.id`` is
                    # the pinned on-disk slot, so it is stable across releases
                    # and collision-free across the catalogue; the ``kind``
                    # prefix keeps a dict and a frequency source that somehow
                    # shared an id apart.
                    resume_key=_resume_key(spec),
                )

                if self.check_cancelled():
                    summary.cancelled = True
                    with contextlib.suppress(OSError):
                        temp.unlink()
                    break

                if not self._promotion_allowed():
                    with contextlib.suppress(OSError):
                        temp.unlink()
                    if self.is_cancelled:
                        summary.cancelled = True
                    else:
                        detail = self._promotion_blocked_detail()
                        summary.results.append(
                            ResourceDownloadResult(
                                spec_id=spec.id,
                                kind=spec.kind,
                                display_name=spec.display_name,
                                url=spec.url,
                                ok=False,
                                detail=detail,
                            )
                        )
                        self.item_done.emit(spec.id, False, detail)
                    break

                # The bytes are in; everything after this is local work. Said
                # before the importer starts so a multi-minute index build is
                # never mistaken for a stalled download.
                reporter.installing()

                dict_id: str | None = None
                source_id: str | None = None
                removed_dicts: list[tuple[str, str]] = []
                failed_removals: list[tuple[str, str]] = []
                if spec.kind == "dict":
                    # Pin the on-disk slot to the stable catalog id so a title
                    # embedding a changing release date (Jitendex) overwrites in
                    # place instead of forking a new dir every download.
                    result = import_yomitan_zip(
                        temp,
                        self._dicts_root,
                        overwrite=True,
                        cancel_check=lambda: self.is_cancelled,
                        progress=reporter.importing,
                        dict_id=spec.id,
                        before_promote=self._require_promotion_allowed,
                    )
                    dict_id = result.dict_id
                    detail = f"{result.entry_count} entries"
                    # Remove pre-fix date-versioned duplicates now living in
                    # sibling dirs. Never fails the item — a broken sweep is
                    # reported, not raised (sweep is structurally total).
                    removed_dicts, failed_removals = sweep_superseded_dicts(
                        self._dicts_root,
                        keep_id=spec.id,
                        imported_source_name=result.source_name,
                    )
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
                        progress=reporter.importing,
                        overwrite=True,
                        before_promote=self._require_promotion_allowed,
                    )
                    source_id = freq_result.source_id
                    detail = f"{freq_result.entry_count} entries"
                elif spec.kind == "pitch":
                    # Same suffix re-typing as freq: import_pitch_source
                    # dispatches on suffix, but download_to_temp stages ``.part``.
                    # Pin the on-disk slot to the stable catalog id (like dict)
                    # so a re-download overwrites in place.
                    temp = _retype_for_suffix(temp, spec.url)
                    pitch_result = import_pitch_source(
                        temp,
                        self._pitch_root,
                        source_id=spec.id,
                        source_name=spec.display_name,
                        cancel_check=lambda: self.is_cancelled,
                        progress=reporter.importing,
                        overwrite=True,
                        before_promote=self._require_promotion_allowed,
                    )
                    source_id = pitch_result.source_id
                    detail = tr_format(
                        QCoreApplication.translate("ResourceDownloadDialog", "%1 entries"),
                        pitch_result.entry_count,
                    )
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
                        removed_dicts=removed_dicts,
                        failed_removals=failed_removals,
                    )
                )
                self.item_done.emit(spec.id, True, detail)
            except Exception as exc:  # noqa: BLE001 — isolate per-item failures
                # The downloader returned a staged file but its route failed.
                # Downloader-owned failures clean themselves; route failures need
                # this best-effort unlink.
                if temp is not None:
                    with contextlib.suppress(OSError):
                        temp.unlink()
                if self.is_cancelled:
                    summary.cancelled = True
                    break
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

        if self.is_cancelled:
            summary.cancelled = True
        self.finished_summary.emit(summary)

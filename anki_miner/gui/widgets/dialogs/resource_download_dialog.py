"""Background download flow for the recommended resource set.

The download used to run behind an ``ApplicationModal`` ``QProgressDialog``
driven by a nested ``QEventLoop``: the whole application was frozen for the
length of a several-hundred-megabyte transfer, and the one line it showed read
``JMdict: Downloading https://…`` — the URL, with none of the byte counts,
rate or clock the transfer was already measuring.

What replaces it:

* :class:`ResourceDownloadSession` starts the worker and returns immediately.
  The window is modeless and has a **Hide** button; closing it hides it. While
  it is hidden the run stays visible in the status bar's task strip, because the
  session writes into the same :class:`~anki_miner.gui.controllers.task_registry.TaskRegistry`
  every other run reports through.
* The primary label is built from :mod:`~anki_miner.gui.utils.progress_telemetry`
  — ``155.4 MB / 600.0 MB · 4.2 MB/s · Elapsed 00:37 · 01:45 left`` — and never
  contains a URL. Hosts and licences live in the sources area below it.
* Phases come from the worker as data (:class:`ResourcePhase`), so the readout
  says what is actually happening: download, then verify/install, then index,
  then activate.

The session may hold the Settings mutation token through native worker finish,
pinning its captured roots while mining remains usable. Immediately before each
importer, the worker asks the GUI thread to release newly-opened index handles.
The caller supplies an activator; the session calls it once after native finish.
Because other Settings may have changed during transfer, the activator is still
responsible for committing pending settings and using the *live* config. Until
it returns a config, the word *Installed* is not used: a resource that imported
but could not be switched on says
``Imported, but not active — Retry setup`` instead.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from PyQt6.QtCore import QCoreApplication, QLocale, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QProgressBar, QWidget

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskSpec
from anki_miner.gui.utils.progress_telemetry import (
    TransferEstimator,
    format_data_size,
    format_transfer,
)
from anki_miner.gui.widgets.base.enhanced_dialog import EnhancedDialog
from anki_miner.gui.workers.resource_download_worker import (
    ResourceDownloadSummary,
    ResourceDownloadWorker,
    ResourcePhase,
    ResourceProgress,
    ResourcePromotionRequest,
)
from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from collections.abc import Sequence

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.controllers.task_registry import TaskHandle, TaskRegistry
    from anki_miner.gui.utils.progress_telemetry import TransferStats
    from anki_miner.services.resource_catalog import ResourceSpec

logger = logging.getLogger(__name__)

#: Stable task id in the registry. One recommended-resource run at a time.
TASK_ID = "resource-download"

#: Determinate progress is reported in permille so a 600 MB transfer still moves
#: the bar between repaints.
_BAR_SCALE = 1000


@dataclass(frozen=True)
class ResourceDownloadOutcome:
    """What one run ended up doing.

    ``activated`` is the honest half: imports can succeed while activation is
    refused (Settings had an uncommittable edit, another mutation held the
    token), and those two states must never be reported as the same thing.
    """

    config: AnkiMinerConfig
    summary: ResourceDownloadSummary
    activated: bool = False


#: Turns a completed summary into the live config, or ``None`` when activation
#: was refused or failed. The implementation owns committing pending settings
#: and recomputing from the live config — see the module docstring.
Activator = Callable[[ResourceDownloadSummary], "AnkiMinerConfig | None"]
MutationAcquirer = Callable[[], "tuple[AnkiMinerConfig, Callable[[], None]] | None"]
BlockedReporter = Callable[[str], None]


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------


def resource_detail(
    event: ResourceProgress,
    *,
    locale: QLocale,
    stats: TransferStats | None = None,
) -> str:
    """The most specific true thing about one resource at this instant.

    Each phase says only what it can back. The download line is the owner's
    original request — amount of *this* download against its total, the rate,
    the elapsed clock, and a remaining estimate only once the estimator is
    willing to stand behind one. ``Installed`` deliberately appears nowhere:
    that word belongs to activation, which happens after this worker is done.
    """
    if event.phase is ResourcePhase.DOWNLOADING:
        if stats is None:
            return QCoreApplication.translate("ResourceDownloadDialog", "Starting download…")
        return format_transfer(locale, stats)

    if event.phase is ResourcePhase.INSTALLING:
        if not event.downloaded:
            return QCoreApplication.translate("ResourceDownloadDialog", "Verifying and installing…")
        return tr_format(
            QCoreApplication.translate("ResourceDownloadDialog", "%1 downloaded · Verifying and installing…"),
            format_data_size(locale, event.downloaded),
        )

    if event.phase is ResourcePhase.INDEXING:
        return tr_format(
            QCoreApplication.translate("ResourceDownloadDialog", "Building index · %1 entries"),
            locale.toString(event.entries or 0),
        )

    return QCoreApplication.translate("ResourceDownloadDialog", "Activating")


def result_headline(summary: ResourceDownloadSummary, *, activated: bool) -> str:
    """One line for how the run ended.

    The first branch is the point of the whole split: something that imported
    but is not switched on has not been installed, and saying so is what stops
    a user going looking for a dictionary the app cannot use.
    """
    if summary.succeeded and not activated:
        return QCoreApplication.translate("ResourceDownloadDialog", "Imported, but not active — Retry setup")
    if summary.cancelled:
        if summary.succeeded:
            return QCoreApplication.translate(
                "ResourceDownloadDialog", "Resource Download Cancelled (Some Resources Installed)"
            )
        return QCoreApplication.translate("ResourceDownloadDialog", "Resource Download Cancelled")
    if summary.succeeded and not summary.failed:
        return QCoreApplication.translate("ResourceDownloadDialog", "Resources Installed")
    if summary.succeeded:
        return QCoreApplication.translate("ResourceDownloadDialog", "Resources Partially Installed")
    return QCoreApplication.translate("ResourceDownloadDialog", "Resource Download Failed")


def result_lines(summary: ResourceDownloadSummary) -> list[str]:
    """Per-item detail; failed items carry their URL as a manual fallback."""
    lines: list[str] = []
    for result in summary.results:
        if result.ok:
            lines.append(
                tr_format(
                    QCoreApplication.translate("ResourceDownloadDialog", "✓ %1 — %2"),
                    result.display_name,
                    result.detail,
                )
            )
            # Surface the one deletion a download can perform (never silent).
            for _dict_id, name in result.removed_dicts:
                lines.append(
                    tr_format(
                        QCoreApplication.translate("ResourceDownloadDialog", "   Replaced older copy: %1"),
                        name,
                    )
                )
            for _dict_id, name in result.failed_removals:
                lines.append(
                    tr_format(
                        QCoreApplication.translate(
                            "ResourceDownloadDialog",
                            "   Could not remove older copy: %1 — remove it via Settings → Dictionaries",
                        ),
                        name,
                    )
                )
        else:
            lines.append(
                tr_format(
                    QCoreApplication.translate("ResourceDownloadDialog", "✗ %1 — %2\n   Download manually: %3"),
                    result.display_name,
                    result.detail,
                    result.url,
                )
            )

    if summary.cancelled:
        if lines:
            lines.append("")
        if summary.succeeded:
            lines.append(
                QCoreApplication.translate(
                    "ResourceDownloadDialog", "Some resources were installed before cancellation."
                )
            )
        else:
            lines.append(QCoreApplication.translate("ResourceDownloadDialog", "No resources were installed."))
        if summary.not_processed_count:
            lines.append(
                tr_format(
                    QCoreApplication.translate("ResourceDownloadDialog", "Resource items not processed: %1."),
                    summary.not_processed_count,
                )
            )
    elif not lines:
        lines.append(QCoreApplication.translate("ResourceDownloadDialog", "No resources were processed."))
    return lines


def sources_text(specs: Sequence[ResourceSpec]) -> str:
    """Host and licence per resource — the details area, never the label.

    Hosts rather than full URLs: the label above has to stay readable, and a
    user checking *where this came from* is asking about the host and the
    licence, not about a 120-character release asset path.
    """
    return "\n".join(f"{spec.display_name} — {urlparse(spec.url).netloc} — {spec.license_note}" for spec in specs)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


class ResourceDownloadWindow(EnhancedDialog):
    """Modeless view of one run. Owns no worker, no timer and no progress state.

    Closing it is Hide, not Cancel, for as long as the run is live: a window
    that cancels a 600 MB transfer because the user pressed Escape is not a
    window anyone can leave open.
    """

    hide_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    retry_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None, specs: Sequence[ResourceSpec]) -> None:
        super().__init__(parent, QCoreApplication.translate("ResourceDownloadDialog", "Recommended Resources"))
        self.setWindowModality(Qt.WindowModality.NonModal)
        # A window that only reports on a download must never be the reason
        # the application is still running (mirrors mini_job_monitor.py).
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._running = True

        self.resource_label = QLabel("")
        self.resource_label.setObjectName("heading3")
        self.resource_label.setWordWrap(True)
        self.add_content(self.resource_label)

        # The primary line: byte counts, rate, elapsed, guarded ETA. Never a URL.
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("helper-text")
        self.detail_label.setWordWrap(True)
        self.add_content(self.detail_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 0)
        self.add_content(self.progress_bar)

        self.results_label = QLabel("")
        self.results_label.setWordWrap(True)
        # Selectable: a failed item prints the URL to fetch by hand, and it is
        # only useful if it can be copied.
        self.results_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.results_label.hide()
        self.add_content(self.results_label)

        self.sources_label = QLabel(sources_text(specs))
        self.sources_label.setObjectName("caption")
        self.sources_label.setWordWrap(True)
        self.add_content(self.sources_label)

        licence_note = QLabel(
            QCoreApplication.translate(
                "ResourceDownloadDialog",
                "Resources are downloaded from their original sources; their licenses apply.",
            )
        )
        licence_note.setObjectName("caption")
        licence_note.setWordWrap(True)
        self.add_content(licence_note)

        self.hide_button = self.add_button(
            QCoreApplication.translate("ResourceDownloadDialog", "Hide"), "ghost", self._on_hide
        )
        self.cancel_button = self.add_button(
            QCoreApplication.translate("ResourceDownloadDialog", "Cancel"), "secondary", self.cancel_requested.emit
        )
        self.retry_button = self.add_button(
            QCoreApplication.translate("ResourceDownloadDialog", "Retry setup"), "primary", self.retry_requested.emit
        )
        # Quiet, not accent: when Retry setup is showing, the accent belongs to
        # the one action that changes anything (D41 — one primary per footer).
        self.close_button = self.add_button(
            QCoreApplication.translate("ResourceDownloadDialog", "Close"), "secondary", self.close
        )
        self.retry_button.hide()
        self.close_button.hide()

    # --- rendering ------------------------------------------------------

    def show_activity(self, title: str, detail: str, fraction: float | None) -> None:
        """Paint one observation. ``fraction`` is None when there is no total."""
        self.resource_label.setText(title)
        self.detail_label.setText(detail)
        if fraction is None:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, _BAR_SCALE)
            self.progress_bar.setValue(int(fraction * _BAR_SCALE))

    def show_cancelling(self) -> None:
        """One verb, then its disabled past tense (D22). No prompt, no second ask."""
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText(QCoreApplication.translate("ResourceDownloadDialog", "Cancelling…"))

    def show_result(self, headline: str, lines: Sequence[str], *, can_retry: bool) -> None:
        """Switch to the terminal state and let the window be closed for real."""
        self._running = False
        self.resource_label.setText(headline)
        self.detail_label.setText("")
        self.progress_bar.hide()
        self.results_label.setText("\n".join(lines))
        self.results_label.show()
        self.hide_button.hide()
        self.cancel_button.hide()
        self.retry_button.setVisible(can_retry)
        self.close_button.show()

    # --- close semantics ------------------------------------------------

    def _on_hide(self) -> None:
        self.hide()
        self.hide_requested.emit()

    def closeEvent(self, event) -> None:
        """Closing a live run hides it; the run itself keeps going."""
        if self._running:
            event.ignore()
            self._on_hide()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        """Escape hides a live run rather than cancelling it."""
        if self._running:
            self._on_hide()
            return
        super().reject()


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class ResourceDownloadSession(QObject):
    """Owns one background download+import+activation run.

    Deliberately not a QWidget and not parented to the window: the window can be
    hidden, and — from the setup wizard — destroyed, while the run continues.
    Worker lifetime is handed to whoever can join it at shutdown via
    ``adopt_worker``; this object only observes and renders.
    """

    #: Emits the ResourceDownloadOutcome, or None when the worker produced no
    #: result at all. Emitted once when the run ends, and once more per
    #: successful **Retry setup** — the summary is unchanged, ``activated`` is
    #: not, and a consumer that stopped listening after the first would keep
    #: showing an imported-but-inactive state that no longer exists.
    finished = pyqtSignal(object)

    #: Registry id this session reports under, so a view holding a session can
    #: recognise its own task without importing the module constant.
    task_id = TASK_ID

    def __init__(
        self,
        parent: QWidget | None,
        config: AnkiMinerConfig,
        *,
        activate: Activator,
        release_resources: Callable[[], bool] | None = None,
        acquire_mutation: MutationAcquirer | None = None,
        blocked: BlockedReporter | None = None,
        task_registry: TaskRegistry | None = None,
        adopt_worker: Callable[[ResourceDownloadWorker], None] | None = None,
        specs: Sequence[ResourceSpec] = RECOMMENDED_DEFAULT_SET,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__()
        self._parent = parent
        self._config = config
        self._activate = activate
        self._release_resources = release_resources
        self._acquire_mutation = acquire_mutation
        self._blocked = blocked
        self._registry = task_registry
        self._adopt_worker = adopt_worker
        self._specs = list(specs)
        # Injected so stall and rate behaviour is testable without sleeping;
        # the estimator itself already refuses to read a clock of its own.
        self._clock = clock

        self._locale = QLocale()
        self._estimator = TransferEstimator()
        self._current_spec: str | None = None
        self._last_event: ResourceProgress | None = None
        self._last_stats: TransferStats | None = None
        self._publishing = False

        self._window: ResourceDownloadWindow | None = None
        self._worker: ResourceDownloadWorker | None = None
        self._handle: TaskHandle | None = None
        self._download_dir: Path | None = None
        self._release_mutation: Callable[[], None] | None = None
        self._summary: ResourceDownloadSummary | None = None
        self._cancel_requested = False
        self._terminal_handled = False
        self._activated = False

    # --- public API -----------------------------------------------------

    @property
    def worker(self) -> ResourceDownloadWorker | None:
        """The running worker, or None before start / after native finish."""
        return self._worker

    @property
    def window(self) -> ResourceDownloadWindow | None:
        """The view, or None once Qt has destroyed it."""
        return self._window

    def start(self) -> bool:
        """Begin the run and return immediately. False means it never started."""
        if self._registry is not None:
            snapshot = self._registry.snapshot(TASK_ID)
            if snapshot is not None and snapshot.is_running:
                self._registry.request_reveal(TASK_ID)
                return False

        if self._acquire_mutation is not None:
            lease = self._acquire_mutation()
            if lease is None:
                self._report_blocked(
                    QCoreApplication.translate(
                        "ResourceDownloadDialog",
                        "Resource settings are busy or could not be saved. Wait for the active task and try again.",
                    )
                )
                return False
            self._config, self._release_mutation = lease

        try:
            if self._release_resources is not None and not self._release_resources():
                self._release_mutation_once()
                self._report_blocked(
                    QCoreApplication.translate(
                        "ResourceDownloadDialog",
                        "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                        "Wait for the active task to finish and try again.",
                    )
                )
                return False

            self._download_dir = Path(tempfile.mkdtemp(prefix="anki_miner_dl_"))

            window = ResourceDownloadWindow(self._parent, self._specs)
            window.cancel_requested.connect(self.cancel)
            window.retry_requested.connect(self._retry_activation)
            window.destroyed.connect(self._on_window_destroyed)
            window.show_activity(
                QCoreApplication.translate("ResourceDownloadDialog", "Recommended resources"),
                QCoreApplication.translate("ResourceDownloadDialog", "Starting download…"),
                None,
            )
            window.show()
            self._window = window

            worker = ResourceDownloadWorker(
                self._specs,
                dicts_root=self._config.dicts_root,
                freqs_root=self._config.freqs_root,
                pitch_root=self._config.pitch_root,
                download_dir=self._download_dir,
            )
            self._worker = worker
            worker.item_progress.connect(self._on_item_progress)
            worker.finished_summary.connect(self._on_summary)
            worker.finished.connect(self._on_thread_finished)
            promotion_requested = getattr(worker, "promotion_requested", None)
            require_promotion_approval = getattr(worker, "require_promotion_approval", None)
            if promotion_requested is not None and callable(require_promotion_approval):
                promotion_requested.connect(self._on_promotion_requested)
                require_promotion_approval()

            if self._registry is not None:
                self._handle = self._registry.start(
                    TaskSpec(
                        task_id=TASK_ID,
                        title=QCoreApplication.translate("ResourceDownloadDialog", "Recommended resources"),
                        owner=CapabilityTarget("settings", "dictionaries"),
                    )
                )
                self._registry.snapshot_changed.connect(self._on_registry_tick)
                # A surface with no route to this worker -- the mini job monitor --
                # can still ask for the run to stop. The asking arrives here; the
                # stopping is still done by this session, through the same Cancel
                # the window's own button uses.
                self._registry.cancel_requested.connect(self._on_cancel_requested)

            if self._adopt_worker is not None:
                self._adopt_worker(worker)

            worker.start()
        except Exception:
            self._cleanup()
            self._release_mutation_once()
            raise
        return True

    def reveal(self) -> None:
        """Bring a hidden window back — what the task strip navigates to."""
        window = self._window
        if window is None:
            return
        with contextlib.suppress(RuntimeError):
            window.show()
            window.raise_()
            window.activateWindow()

    def _on_cancel_requested(self, task_id: str) -> None:
        """Honour a registry cancel request aimed at this session."""
        if task_id == TASK_ID:
            self.cancel()

    def cancel(self) -> None:
        """Request cancellation. One verb: no confirmation prompt (D22)."""
        if self._cancel_requested or self._terminal_handled:
            return
        self._cancel_requested = True
        # Told to the registry as well as to this window, so a surface watching
        # the run from elsewhere freezes its numbers and starts the wait clock
        # at the same instant (D22) rather than going on quoting bytes the run
        # is about to abandon.
        if self._handle is not None:
            self._handle.cancelling()
        worker = self._worker
        if worker is not None:
            with contextlib.suppress(RuntimeError):
                worker.cancel()
        self._with_window(lambda window: window.show_cancelling())

    # --- worker signals -------------------------------------------------

    def _on_item_progress(self, event: object) -> None:
        if not isinstance(event, ResourceProgress) or self._terminal_handled:
            return

        if event.spec_id != self._current_spec:
            # A new resource is a new transfer: a rate carried over from the
            # previous one would be describing bytes that are no longer moving.
            self._current_spec = event.spec_id
            self._estimator = TransferEstimator()
            self._last_stats = None
            if self._handle is not None:
                index = next((i for i, s in enumerate(self._specs, 1) if s.id == event.spec_id), 1)
                self._handle.stage(index=index, total=len(self._specs), name=event.display_name)

        if event.phase is ResourcePhase.DOWNLOADING:
            self._last_stats = self._estimator.update(
                downloaded=event.downloaded,
                total=event.total_bytes,
                now=self._clock(),
            )
        else:
            self._last_stats = None

        self._last_event = event
        self._publish(event, self._last_stats)

    def _on_summary(self, summary: object) -> None:
        if isinstance(summary, ResourceDownloadSummary) and self._summary is None:
            self._summary = summary

    def _on_promotion_requested(self, request: object) -> None:
        """Recheck open indexed-resource handles on the GUI thread."""
        if not isinstance(request, ResourcePromotionRequest):
            return
        try:
            allowed = self._release_resources is None or self._release_resources()
        except Exception:  # noqa: BLE001 - release failure must fail closed
            logger.exception("Resource release handshake failed")
            allowed = False
        request.resolve(allowed)

    def _on_thread_finished(self) -> None:
        """Terminal handling, gated on the worker's NATIVE thread finish.

        Not on ``finished_summary``: that arrives from inside ``run()``, while
        the thread is still alive, and acting on it would activate resources
        while the worker still holds the sqlite handles it wrote them with.
        """
        if self._terminal_handled:
            return
        self._terminal_handled = True
        self._worker = None
        self._release_mutation_once()

        if self._registry is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                self._registry.snapshot_changed.disconnect(self._on_registry_tick)

        summary = self._summary
        if summary is None and self._cancel_requested:
            summary = ResourceDownloadSummary(cancelled=True, requested_count=len(self._specs))

        if summary is None:
            self._finish_task(TaskOutcome.FAILED)
            self._with_window(
                lambda window: window.show_result(
                    QCoreApplication.translate("ResourceDownloadDialog", "Resource Download Failed"),
                    [
                        QCoreApplication.translate(
                            "ResourceDownloadDialog",
                            "The download worker finished without a completion result.",
                        )
                    ],
                    can_retry=False,
                )
            )
            self._cleanup()
            self.finished.emit(None)
            return

        if self._cancel_requested and not summary.cancelled:
            summary = ResourceDownloadSummary(
                results=summary.results,
                cancelled=True,
                requested_count=summary.requested_count,
                dicts_root=summary.dicts_root,
                freqs_root=summary.freqs_root,
                pitch_root=summary.pitch_root,
            )
        self._summary = summary

        config = self._run_activation(summary)
        self._finish_task(
            TaskOutcome.CANCELLED
            if summary.cancelled
            else (TaskOutcome.SUCCEEDED if self._activated else TaskOutcome.FAILED)
        )
        self._render_result(summary)
        self._cleanup()
        self.finished.emit(ResourceDownloadOutcome(config=config, summary=summary, activated=self._activated))

    # --- activation -----------------------------------------------------

    def _run_activation(self, summary: ResourceDownloadSummary) -> AnkiMinerConfig:
        """Switch the imported resources on, or leave them imported-but-inactive."""
        if not summary.succeeded:
            return self._config

        self._with_window(
            lambda window: window.show_activity(
                QCoreApplication.translate("ResourceDownloadDialog", "Recommended resources"),
                QCoreApplication.translate("ResourceDownloadDialog", "Activating"),
                None,
            )
        )
        try:
            activated_config = self._activate(summary)
        except Exception:  # noqa: BLE001 — a refusing activator must not kill the session
            logger.exception("Resource activation failed")
            activated_config = None

        if activated_config is None:
            return self._config
        self._activated = True
        self._config = activated_config
        return activated_config

    def _retry_activation(self) -> None:
        """Re-run activation only. Never re-downloads: the bytes are already in."""
        summary = self._summary
        if summary is None or self._activated:
            return
        config = self._run_activation(summary)
        self._render_result(summary)
        self.finished.emit(ResourceDownloadOutcome(config=config, summary=summary, activated=self._activated))

    # --- rendering ------------------------------------------------------

    def _publish(self, event: ResourceProgress, stats: TransferStats | None) -> None:
        """Render one observation to the window and the registry, once."""
        detail = resource_detail(event, locale=self._locale, stats=stats)
        fraction = stats.fraction if stats is not None else None
        self._with_window(lambda window: window.show_activity(event.display_name, detail, fraction))

        if self._handle is None:
            return
        self._publishing = True
        try:
            if event.phase is ResourcePhase.DOWNLOADING:
                self._handle.count(current=event.downloaded, total=event.total_bytes, detail=detail)
            else:
                self._handle.count(current=event.entries or 0, total=None, detail=detail)
        finally:
            self._publishing = False

    def _on_registry_tick(self, task_id: str) -> None:
        """Re-render on the registry's own tick so a stall is visibly a stall.

        ``format_transfer`` withdraws the rate and ETA once bytes stop moving,
        but only if something keeps asking. The registry already owns the one
        second ticker, so this borrows it rather than starting a second one.
        """
        if self._publishing or task_id != TASK_ID or self._terminal_handled:
            return
        event = self._last_event
        if event is None or event.phase is not ResourcePhase.DOWNLOADING:
            return
        stats = self._estimator.update(
            downloaded=event.downloaded,
            total=event.total_bytes,
            now=self._clock(),
        )
        self._last_stats = stats
        detail = resource_detail(event, locale=self._locale, stats=stats)
        self._with_window(lambda window: window.show_activity(event.display_name, detail, stats.fraction))

    def _render_result(self, summary: ResourceDownloadSummary) -> None:
        headline = result_headline(summary, activated=self._activated)
        lines = result_lines(summary)
        can_retry = bool(summary.succeeded) and not self._activated
        self._with_window(lambda window: window.show_result(headline, lines, can_retry=can_retry))
        self.reveal()

    # --- plumbing -------------------------------------------------------

    def _finish_task(self, outcome: TaskOutcome) -> None:
        if self._handle is not None:
            self._handle.finish(outcome)

    def _with_window(self, action: Callable[[ResourceDownloadWindow], None]) -> None:
        """Run ``action`` on the window if it still exists.

        The window can be destroyed with its parent at any point — the setup
        wizard closes while a run continues — so every paint is guarded rather
        than assuming the view outlives the run.
        """
        window = self._window
        if window is None:
            return
        with contextlib.suppress(RuntimeError):
            action(window)

    def _on_window_destroyed(self) -> None:
        self._window = None

    def _report_blocked(self, message: str) -> None:
        if self._blocked is not None:
            self._blocked(message)
            return
        status_label = getattr(self._parent, "status_label", None)
        if isinstance(status_label, QLabel):
            status_label.setText(message)

    def _release_mutation_once(self) -> None:
        release = self._release_mutation
        self._release_mutation = None
        if release is not None:
            release()

    def _cleanup(self) -> None:
        """Drop the staging directory once the worker's thread has truly exited."""
        if self._download_dir is not None:
            shutil.rmtree(self._download_dir, ignore_errors=True)
            self._download_dir = None


def start_resource_download(
    parent: QWidget | None,
    config: AnkiMinerConfig,
    *,
    activate: Activator,
    release_resources: Callable[[], bool] | None = None,
    acquire_mutation: MutationAcquirer | None = None,
    blocked: BlockedReporter | None = None,
    task_registry: TaskRegistry | None = None,
    adopt_worker: Callable[[ResourceDownloadWorker], None] | None = None,
    specs: Sequence[ResourceSpec] = RECOMMENDED_DEFAULT_SET,
) -> ResourceDownloadSession | None:
    """Start a background recommended-resource run; None means it never started.

    ``release_resources`` drops live dictionary sqlite handles before the worker
    runs and again immediately before each importer promotion. The import
    overwrites a pinned slot in place and the sweep deletes superseded dirs, so
    on Windows an open
    ``IndexedDictProvider`` connection would make the rename/rmtree fail with
    "Access denied" (Issues #30/#32). If it returns False, indexed resources are
    in use — report through the caller's nonmodal surface and abort without
    touching the managed slot.

    The returned session must be retained by the caller: it is not Qt-parented,
    because the widget that started it may be gone long before the run ends.

    ``specs`` narrows the run to a subset of the catalog. It defaults to the
    whole set, so a caller that offers no choice — the Tools menu — is
    unchanged.
    """
    session = ResourceDownloadSession(
        parent,
        config,
        activate=activate,
        release_resources=release_resources,
        acquire_mutation=acquire_mutation,
        blocked=blocked,
        task_registry=task_registry,
        adopt_worker=adopt_worker,
        specs=specs,
    )
    return session if session.start() else None

"""Shared base for the Anki-plan tool tabs (Card Backfill / Deck Filter).

Both run Scan (read-only, builds a plan shown in a preview table) then Apply
(writes exactly that plan) against a deck already in Anki. Not
``_ToolTabBase``: that base is file-processing chrome.

A subclass sets the attributes annotated on the class before any hoisted slot
runs, overrides the hooks that raise ``NotImplementedError``, and builds
``self._strings`` itself via ``self.tr(...)`` so every literal keeps that tab's
tr-context (the ``_ToolTabStrings`` precedent).

Nothing here logs: ``_load_decks`` and the two error slots stay in the tab
modules, where tests patch ``AnkiService`` / ``FetchDecksWorker`` and the log
records must keep naming the tab's own module.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PyQt6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QLabel,
    QProgressBar,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.qt_helpers import configure_data_view, data_row_height, urls_from_event
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets.base import TaskPublisherMixin, WorkflowActionBar
from anki_miner.gui.widgets.enhanced import ModernButton

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.workers.base_worker import CancellableWorker, SingleCallWorker

#: How much of the plan the preview must always show. Counted in rows rather
#: than pixels: a flat pixel floor holds fewer and fewer rows as the text scale
#: grows, which is Issue #102's class of bug rather than its fix.
PREVIEW_MIN_VISIBLE_ROWS = 8


@dataclass(frozen=True)
class _PlanTabStrings:
    """Per-tab translated lines consumed by the hoisted slots.

    Built in each subclass via ``self.tr(...)`` so every literal stays in that
    tab's tr-context (see the module docstring).
    """

    #: The Apply run's live status line ("Applying…" / "Copying…"). A cancel
    #: that lands while it is still showing replaces it outright.
    applying: str
    cancelling: str
    cancelled: str
    #: Why Apply dropped the held plan instead of writing it.
    settings_changed: str
    #: The status line while the deck list could not be fetched.
    couldnt_fetch_decks: str


class _AnkiPlanTabBase(TaskPublisherMixin, QWidget):
    """Behaviour shared by the Anki-plan tool tabs. See module docstring."""

    # --- Attributes the subclass provides (declared for the type checker) ---
    config: AnkiMinerConfig
    _strings: _PlanTabStrings
    worker_thread: CancellableWorker | None
    _plan: object | None
    _scan_warnings: tuple[str, ...]
    _decks_loaded: bool
    _deck_fetch_failed: bool
    _deck_worker: SingleCallWorker | None
    _run_failed: bool
    scan_button: ModernButton
    apply_button: ModernButton
    cancel_button: ModernButton
    summary_label: QLabel
    preview_table: QTableWidget
    action_bar: WorkflowActionBar
    progress_bar: QProgressBar
    status_label: QLabel

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _deck_combo(self) -> QComboBox:
        """The combo the deck list fills; a refused drop points the user at it."""
        raise NotImplementedError

    def _drop_refusal(self) -> str:
        """The one reason this screen gives for refusing a dropped payload."""
        raise NotImplementedError

    def _load_decks(self) -> None:
        """Start the deck-list fetch (kept in the tab module; see module docstring)."""
        raise NotImplementedError

    def _set_running(self, running: bool) -> None:
        """Gate the screen's controls for a run starting or ending."""
        raise NotImplementedError

    def _quiet_extras(self) -> tuple[QAbstractButton, ...]:
        """Bar actions that sit after the quiet verb whatever the stage."""
        return ()

    def _extra_close_workers(self) -> tuple[CancellableWorker | None, ...]:
        """Handles beyond the run and the deck fetch that close must join."""
        return ()

    # ------------------------------------------------------------------
    # Preview, run status and the pinned bar
    # ------------------------------------------------------------------

    def _apply_preview_height_floor(self) -> None:
        """Floor the preview at whole rows of the font it is actually rendering.

        Issue #102 gave the table a flat 240px floor so the chrome above could
        not crush it. That floor holds eight rows at the default text size and
        four at 150%, so the same crushing returns the moment the user scales
        text. Measuring the floor in rows keeps the guarantee the issue asked
        for at every scale.
        """
        header = self.preview_table.horizontalHeader()
        header_h = header.sizeHint().height() if header is not None else 0
        frame = 2 * self.preview_table.frameWidth()
        self.preview_table.setMinimumHeight(
            header_h + frame + PREVIEW_MIN_VISIBLE_ROWS * data_row_height(self.preview_table)
        )

    def changeEvent(self, a0) -> None:  # noqa: N802 - Qt override
        """Re-derive the preview's row metrics when the UI zoom changes.

        Zoom applies live, so a row height and a floor computed once at
        construction are stale from the next Settings save onward.
        """
        super().changeEvent(a0)
        if a0 is not None and a0.type() == QEvent.Type.FontChange and hasattr(self, "preview_table"):
            configure_data_view(self.preview_table)
            self._apply_preview_height_floor()

    def _create_run_status(self) -> QWidget:
        """The one-line status and thin bar that sit directly above the actions."""
        strip = QWidget()
        strip_layout = QVBoxLayout(strip)
        strip_layout.setContentsMargins(SPACING.sm, 0, SPACING.sm, 0)
        strip_layout.setSpacing(SPACING.xxs)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        strip_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        strip_layout.addWidget(self.status_label)

        return strip

    def _sync_action_prominence(self) -> None:
        """Put the action the user can actually take next on the right.

        Before a valid preview there is only one honest move — Scan — and Apply
        has nothing to apply. Once a preview exists Apply becomes the point of
        the screen, and Scan stays visible so a rescan never needs the page
        rebuilt.
        """
        has_plan = self._plan is not None
        primary = self.apply_button if has_plan else self.scan_button
        quiet = self.scan_button if has_plan else self.apply_button
        primary.set_variant("primary")
        quiet.set_variant("secondary")
        self.action_bar.set_actions(primary, (self.cancel_button, quiet, *self._quiet_extras()))

    def _drop_plan(self) -> None:
        """Forget the held plan and clear everything the screen showed from it.

        The pinned bar is left to the caller: a config change and a stale Apply
        re-sync it at once, while the Apply receipts leave it to the
        ``finished`` slot, which re-gates the whole screen.
        """
        self._plan = None
        self._scan_warnings = ()
        self.preview_table.setRowCount(0)
        self.apply_button.setEnabled(False)
        self.summary_label.setText("")

    def _drop_stale_plan(self, plan_version: int) -> bool:
        """Drop a plan scanned under older settings and say why; report whether it did.

        A plan's computed values are all config-derived, so applying one after
        a settings change would write values the user never previewed.
        """
        if plan_version == self.config.config_version:
            return False
        self._drop_plan()
        self._sync_action_prominence()
        self.status_label.setText(self._strings.settings_changed)
        return True

    # ------------------------------------------------------------------
    # Drag and drop (D50): these screens take no payload, and say so
    # ------------------------------------------------------------------

    def _may_answer_a_drop(self) -> bool:
        """Whether the status line is free to carry a drop refusal.

        During a run that line is the only account of what the run is doing, so
        a stray drag must not overwrite ``Scanning…`` with a note about decks.
        The drag is simply not accepted then, and the cursor already says no.
        """
        return self.worker_thread is None

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # noqa: N802 - Qt override
        """Accept the drag so the refusal can be delivered, and state it now.

        The screen reads a deck the user picks above; there is no file it could
        take. Accepting only buys the chance to answer -- an ignored drag is the
        silent non-acceptance D50 exists to remove.
        """
        if event is None or not self._may_answer_a_drop():
            return
        if not urls_from_event(event):
            return
        event.acceptProposedAction()
        self.status_label.setText(self._drop_refusal())

    def dragLeaveEvent(self, event: QDragLeaveEvent | None) -> None:  # noqa: N802 - Qt override
        """Take the refusal back down when the drag moves off the screen."""
        if self.status_label.text() == self._drop_refusal():
            self.status_label.setText("")
        if event is not None:
            event.accept()

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802 - Qt override
        """Refuse the payload and point at the control that does the choosing."""
        if event is None:
            return
        if self._may_answer_a_drop():
            self.status_label.setText(self._drop_refusal())
            self._deck_combo().setFocus(Qt.FocusReason.OtherFocusReason)
        event.ignore()

    # ------------------------------------------------------------------
    # Deck dropdown (lazy fetch on first show)
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.ensure_decks()

    def ensure_decks(self) -> None:
        """Fetch the deck list unless one has already arrived.

        Guarded on the list ARRIVING, not on having asked. ``get_deck_names``
        answers an unreachable Anki with an empty list, so latching on the
        attempt left "Couldn't fetch deck names from Anki" on screen for the
        life of the process once Anki was started after Anki Miner — this tab
        has no Refresh button to escape with.

        Also the slot for ``MainWindow.anki_reachable``: a validation sweep that
        has just found Anki is the only thing that can retry while this tab is
        the visible one and its ``showEvent`` will not fire again.
        """
        if self._decks_loaded or still_running(self._deck_worker):
            return
        self._load_decks()

    def _on_decks_fetched(self, decks: list) -> None:
        if decks:
            # Runs at most once: ensure_decks() stops asking from here on, so
            # the combo can never accumulate a second copy of the deck list.
            self._decks_loaded = True
            self._deck_combo().addItems([str(d) for d in decks])
            if self._deck_fetch_failed:
                self._deck_fetch_failed = False
                self.status_label.setText("")
        else:
            self._deck_fetch_failed = True
            self.status_label.setText(self._strings.couldnt_fetch_decks)

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(self) -> Iterator[CancellableWorker]:
        # The lazy fetches run blocking AnkiConnect calls (get_deck_names has a
        # 15s timeout); abandoning them to Qt teardown aborts with "QThread:
        # Destroyed while thread is still running", so they are surfaced for the
        # close-join policy alongside the run worker.
        #
        # ``still_running``, never a raw ``isRunning()``: a finished handle is
        # not cleared, and ``run_off_thread`` deleteLater()s the worker it
        # returns, so a handle can be a live Python wrapper around a destroyed
        # C++ QThread. ``isRunning()`` on it raises RuntimeError -- out of
        # closeEvent, into the excepthook dialog, and past the config save at
        # the end of MainWindow.closeEvent.
        for worker in (self.worker_thread, self._deck_worker, *self._extra_close_workers()):
            if still_running(worker):
                assert worker is not None
                yield worker

    # ------------------------------------------------------------------
    # Worker plumbing
    # ------------------------------------------------------------------

    def _cancel_published_task(self) -> None:
        """Route a registry cancel request into this screen's own Cancel."""
        self._cancel()

    def _cancel(self) -> None:
        """Cancel the run: one verb, no prompt, and the button says it is waiting."""
        if self.worker_thread is not None and self.worker_thread.isRunning():
            self._publish_task_cancelling()
            self.worker_thread.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText(self._strings.cancelling)

    def _on_progress(self, done: int, total: int) -> None:
        if total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
        self._publish_task_count(current=done, total=total or None, detail="")

    def _on_apply_cancelled(self) -> None:
        self._drop_plan()
        receipt = self.status_label.text()
        if receipt in {self._strings.applying, self._strings.cancelling}:
            self.status_label.setText(self._strings.cancelled)
        elif not receipt.startswith(self._strings.cancelled):
            self.status_label.setText(f"{self._strings.cancelled} {receipt}")

    def _on_worker_finished(self) -> None:
        self._set_running(False)
        cancelled = self.worker_thread is not None and self.worker_thread.is_cancelled
        # The scan worker declares no ``cancelled`` signal and returns silently,
        # so this is the only place that can close out a cancelled scan. Guarded
        # on the exact live text: the Apply path has already written its partial
        # receipt here by the time ``finished`` arrives.
        if cancelled and self.status_label.text() == self._strings.cancelling:
            self.status_label.setText(self._strings.cancelled)
        self._publish_task_finish(self._task_outcome(cancelled=cancelled, failed=self._run_failed))
        self._run_failed = False
        self.worker_thread = None

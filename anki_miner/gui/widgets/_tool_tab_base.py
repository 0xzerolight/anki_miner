"""Shared base for the file-processing tool tabs (Generate / Retime / Condense).

Hoists the verbatim-identical worker-signal slots, the output-location slots, the
progress-section chrome, and the close contract shared by
:class:`~anki_miner.gui.widgets.subtitle_creation_tab.SubtitleCreationTab`,
:class:`~anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeTab`, and
:class:`~anki_miner.gui.widgets.condense_tab.CondenseTab`.

Per-tool input/options sections, availability gating, and the ``_on_<verb>``
launcher stay subclass responsibilities.

Subclass contract — a subclass MUST provide, before any hoisted slot runs:
  * instance attrs ``worker_thread``, ``_custom_output_dir``, ``_cancelled``,
    ``output_location_label``, ``clear_output_button``, ``cancel_button``,
    ``progress_widget``, ``log_widget`` (the last two via
    :meth:`_create_progress_section`);
  * ``self._primary_button`` — the tool's action button (set when building the
    Actions section);
  * ``self._strings`` — a :class:`_ToolTabStrings` built in the SUBCLASS via
    ``self.tr(...)``. The literals are kept in the subclass on purpose: each
    tab's ``self.tr`` binds the string to that tab's own tr-context, so the
    translation catalogs keep one entry per tab (no context churn / payload
    loss) even though the consuming logic lives here;
  * an override of :meth:`_item_total`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from anki_miner.gui.utils import file_dialogs, session_state
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.utils.keyboard_shortcuts import primary_action_shortcut
from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssue,
    ScreenIssueHost,
    TaskPublisherMixin,
    WorkflowActionBar,
    configure_card_layout,
    install_workflow_shell,
)
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.models import TerminalOutcome
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.controllers.task_registry import TaskRegistry
    from anki_miner.gui.workers.base_worker import CancellableWorker


@dataclass(frozen=True)
class _ToolTabStrings:
    """Per-tab translated labels consumed by the hoisted slots.

    Built in each subclass via ``self.tr(...)`` so every literal stays in that
    tab's tr-context (see the module docstring). ``output_default`` is the
    "no custom folder" placeholder, which differs per tab.
    """

    progress: str
    done: str
    done_prefix: str
    skipped: str
    skipped_prefix: str
    cancel: str
    cancelling: str
    cancelled: str
    failed: str
    #: Banner summary for a file the run could not process. The failing file's
    #: message goes in Details, never here (D24).
    run_problem: str
    complete_template: str
    #: Completion line when some (but not all) files were skipped:
    #: ``%1`` = processed count, ``%2`` = skipped count.
    complete_skipped_template: str
    #: Completion line when EVERY file was skipped: ``%1`` = skipped count.
    #: Per-tab wording so the remedy can be tool-specific (e.g. Retime names
    #: the Overwrite checkbox).
    all_skipped_template: str
    select_output_folder: str
    output_default: str
    #: What this tool's run is called on a surface that is not this screen
    #: (the status bar, the pinned action bar). Empty publishes nothing.
    task_title: str = ""


class _ToolTabBase(TaskPublisherMixin, ScreenIssueHost, QWidget):
    """Behaviour shared by the file-processing tool tabs. See module docstring."""

    # --- Attributes the subclass provides (declared for the type checker) ---
    _strings: _ToolTabStrings
    _primary_button: ModernButton
    worker_thread: CancellableWorker | None
    _custom_output_dir: Path | None
    _cancelled: bool
    output_location_label: QLabel
    clear_output_button: ModernButton
    cancel_button: ModernButton
    progress_widget: ProgressWidget
    log_widget: LogWidget
    _availability_worker: SingleCallWorker | None = None
    _availability_generation: int = 0
    #: Files skipped in the current run (reset when the run starts). Counted
    #: here rather than read back from the worker so the completion line never
    #: races the worker thread's teardown.
    _run_skipped: int = 0

    #: Stable session key for this tool's remembered OUTPUT folder (D7), e.g.
    #: ``"tools.condense.output"``. Left empty by a subclass that does not want
    #: its output folder remembered; the chooser then behaves as it always did.
    OUTPUT_HISTORY_KEY: str = ""

    def bind_task_registry(self, registry: TaskRegistry) -> None:
        """Bind both global task views and this screen's elapsed display."""
        super().bind_task_registry(registry)
        self.progress_widget.bind_task(registry, self.TASK_ID)

    def _run_availability_scan(
        self,
        work: Callable[[], object],
        on_done: Callable[[object], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Run the latest availability scan off-thread and discard stale results."""
        generation = getattr(self, "_availability_generation", 0) + 1
        self._availability_generation = generation
        previous = getattr(self, "_availability_worker", None)
        if still_running(previous):
            assert previous is not None
            previous.cancel()

        def _on_done(result: object) -> None:
            if generation == self._availability_generation:
                with contextlib.suppress(RuntimeError):
                    on_done(result)

        def _on_error(message: str) -> None:
            if generation == self._availability_generation:
                with contextlib.suppress(RuntimeError):
                    on_error(message)

        self._availability_worker = run_off_thread(self, work, _on_done, _on_error)

    # ------------------------------------------------------------------
    # Progress-section chrome
    # ------------------------------------------------------------------

    def _create_progress_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        configure_card_layout(layout)

        layout.addWidget(SectionHeader(self._strings.progress))

        self.progress_widget = ProgressWidget()
        layout.addWidget(self.progress_widget)

        # ``install_workflow_shell`` moves it into the Activity drawer (D6).
        self.log_widget = LogWidget()
        # The Activity console already carries a typed problem channel; a second
        # "something failed" signal would be two answers to one question (D24).
        self.log_widget.problem_logged.connect(self._on_log_problem)

        group.setLayout(layout)
        return group

    # ------------------------------------------------------------------
    # Pinned action bar (D6)
    # ------------------------------------------------------------------

    #: This tool's pinned action bar, or ``None`` before it is installed.
    action_bar: WorkflowActionBar | None = None

    def _install_action_bar(
        self,
        layout: QVBoxLayout,
        scroll: QScrollArea,
        content: QWidget,
        kind: PageWidth,
    ) -> WorkflowActionBar:
        """Frame this tool's page around a pinned bar carrying its own buttons.

        The tools all name the same two controls — ``_primary_button`` and
        ``cancel_button`` — so the bar is wired here rather than three times.
        The button objects are the subclass's own, keeping each verb's label in
        its own translation context.

        Args:
            layout: The tab's top-level layout.
            scroll: The page's scroll area, not yet given its widget.
            content: The column of cards, fully populated.
            kind: The page's declared ``PAGE_WIDTH``.
        """
        bar = install_workflow_shell(layout, scroll, content, kind, log=self.log_widget)
        bar.set_actions(self._primary_button, (self.cancel_button,))
        self.action_bar = bar
        # Ctrl+Enter runs the tool, scoped to this page (D48-B).
        primary_action_shortcut(self, bar.trigger_primary)
        return bar

    def _on_log_problem(self, level: str, message: str) -> None:
        """Raise a logged ERROR to the screen banner.

        WARNING stays in the log on purpose: a long run produces many, and a
        banner that rewrites itself once per warning is noise rather than a
        report. An ERROR is a file that did not get processed.
        """
        if level != "ERROR":
            return
        self.show_screen_issue(ScreenIssue(summary=self._strings.run_problem, details=message))

    # ------------------------------------------------------------------
    # Output location slots
    # ------------------------------------------------------------------

    def _on_choose_output(self) -> None:
        """Pick a custom output folder, reopening where this tool last wrote.

        Output has its own history key: where a tool's results go is rarely
        where its inputs live, so sharing one anchor would send the user back to
        the source library every time.
        """
        current = self._custom_output_dir

        def _on_picked(folder: str) -> None:
            if folder:
                session_state.remember_accepted_path(self.OUTPUT_HISTORY_KEY, folder, file_mode=False)
                self._custom_output_dir = Path(folder)
                self.output_location_label.setText(folder)
                self.clear_output_button.show()

        file_dialogs.pick_directory(
            self,
            self._strings.select_output_folder,
            resolve_start_dir(
                str(current) if current is not None else None,
                file_mode=False,
                remembered_dir=session_state.remembered_directory(self.OUTPUT_HISTORY_KEY),
            ),
            on_done=_on_picked,
        )

    def _on_clear_output(self) -> None:
        self._custom_output_dir = None
        self.output_location_label.setText(self._strings.output_default)
        self.clear_output_button.hide()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _begin_tool_run(self, total: int) -> None:
        """Open a run: clear the cancel flag and publish it to the registry.

        Publishing is what gives this screen a live wait-clock and the pinned
        bar's stage/progress; without it Cancel froze the bar and then went
        silent (D22, D17).

        Args:
            total: Real number of files or pairs this run will process.
        """
        self._cancelled = False
        self._run_skipped = 0
        self._publish_task_start(self._strings.task_title, total=total)

    # ------------------------------------------------------------------
    # Worker signal slots
    # ------------------------------------------------------------------

    def _on_file_progress(self, idx: int, pct: int, message: str) -> None:
        # The bar counts finished files; the intra-file percentage a tool
        # reports is shown in the message instead of being folded into the bar,
        # where it made a long file look like a stalled run.
        self.progress_widget.set_composed(idx, 0, self._item_total(), message)
        self._publish_task_count(current=idx, total=self._item_total(), detail=message)

    def _on_file_finished(self, idx: int, out_path: object, error_str: object) -> None:
        # Whole-file advance in the same percent unit system as set_composed
        # (a count-unit set_progress here would flip the ETA denominator).
        total = self._item_total()
        if total:
            self.progress_widget.set_percent(int((idx + 1) / total * 100))
        self._publish_task_count(current=idx + 1, total=total or None, detail="")
        if error_str:
            self.log_widget.append_error(str(error_str))
        else:
            path_label = str(out_path) if out_path else ""
            self.log_widget.append_success(
                self._strings.done_prefix + Path(path_label).name if path_label else self._strings.done
            )

    def _on_file_skipped(self, idx: int, out_path: object, reason: str = "") -> None:
        # Advance the progress bar just like a finished file.
        total = self._item_total()
        if total:
            self.progress_widget.set_percent(int((idx + 1) / total * 100))
        self._run_skipped += 1
        # The reason rides the task detail (Activity / global surfaces), not
        # the old empty string — a skip must say why it skipped.
        self._publish_task_count(current=idx + 1, total=total or None, detail=reason)
        path_label = str(out_path) if out_path else ""
        line = self._strings.skipped_prefix + Path(path_label).name if path_label else self._strings.skipped
        if reason:
            line = f"{line} — {reason}"
        self.log_widget.append_info(line)

    def _on_queue_finished(self, outcome: object = TerminalOutcome.SUCCESS) -> None:
        cancelled = self._cancelled or outcome is TerminalOutcome.CANCELLED
        total = self._item_total()
        skipped = self._run_skipped
        # A run where every item was skipped is not a success on the global
        # surfaces (Activity drawer/pinned bar/notification) even though the
        # on-screen status names the remedy instead of an error. Cancel still
        # wins: a cancel that happened to skip everything stays CANCELLED.
        all_skipped = bool(total) and skipped >= total and outcome is TerminalOutcome.SUCCESS
        self._publish_task_finish(
            self._task_outcome(
                cancelled=cancelled,
                failed=outcome in (TerminalOutcome.PARTIAL, TerminalOutcome.FAILED) or all_skipped,
            )
        )
        self._primary_button.setEnabled(True)
        self.cancel_button.hide()
        # Reset for the next run's cancel button.
        self.cancel_button.setText(self._strings.cancel)
        self.cancel_button.setEnabled(True)
        if self._cancelled or outcome is TerminalOutcome.CANCELLED:
            # No reset(): the frozen bar still says how many files got done
            # before the user stopped it.
            self.progress_widget.set_status(self._strings.cancelled)
        elif outcome in (TerminalOutcome.PARTIAL, TerminalOutcome.FAILED):
            self.progress_widget.reset()
            self.progress_widget.set_status(self._strings.failed)
        else:
            # An honest completion line: a skipped file was not "processed".
            # All-skipped runs (the same-folder Retime case) name the remedy
            # instead of claiming success.
            if all_skipped:
                message = tr_format(self._strings.all_skipped_template, skipped)
            elif skipped:
                message = tr_format(self._strings.complete_skipped_template, total - skipped, skipped)
            else:
                message = tr_format(self._strings.complete_template, total)
            self.progress_widget.show_completion(message)

    def _on_run_error(self, message: str) -> None:
        self.log_widget.append_error(message)

    def _on_worker_finished(self) -> None:
        """Release the QThread once it has actually exited."""
        worker = self.worker_thread
        if worker is not None:
            worker.deleteLater()
            self.worker_thread = None

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _cancel_published_task(self) -> None:
        """Route a registry cancel request into this screen's own Cancel."""
        self._on_cancel()

    def _on_cancel(self) -> None:
        """Cancel the run: one verb, no prompt, no invented progress after it."""
        self._cancelled = True
        # Told to the registry first, so every surface watching this run freezes
        # its numbers and starts the wait clock at the same instant (D22).
        self._publish_task_cancelling()
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText(self._strings.cancelling)
        self.cancel_button.setEnabled(False)
        self.progress_widget.freeze()
        self.progress_widget.set_status(self._strings.cancelling)

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(self) -> Iterator[CancellableWorker]:
        """Yield the active worker so BackgroundTaskController can join it on close."""
        if still_running(self._availability_worker):
            assert self._availability_worker is not None
            yield self._availability_worker
        if self.worker_thread is not None:
            yield self.worker_thread

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _item_total(self) -> int:
        """Return the total item count for this run (files or pairs)."""
        raise NotImplementedError

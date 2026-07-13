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

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from PyQt6.QtWidgets import QFileDialog, QFrame, QLabel, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.gui.widgets.log_widget import LogWidget
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
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
    complete_template: str
    select_output_folder: str
    output_default: str


class _ToolTabBase(QWidget):
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

    # ------------------------------------------------------------------
    # Progress-section chrome
    # ------------------------------------------------------------------

    def _create_progress_section(self) -> QFrame:
        group = QFrame()
        group.setObjectName("card")
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        layout.addWidget(SectionHeader(self._strings.progress))

        self.progress_widget = ProgressWidget()
        layout.addWidget(self.progress_widget)

        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        group.setLayout(layout)
        return group

    # ------------------------------------------------------------------
    # Output location slots
    # ------------------------------------------------------------------

    def _on_choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self._strings.select_output_folder,
            str(Path.home()),
        )
        if folder:
            self._custom_output_dir = Path(folder)
            self.output_location_label.setText(folder)
            self.clear_output_button.show()

    def _on_clear_output(self) -> None:
        self._custom_output_dir = None
        self.output_location_label.setText(self._strings.output_default)
        self.clear_output_button.hide()

    # ------------------------------------------------------------------
    # Worker signal slots
    # ------------------------------------------------------------------

    def _on_file_progress(self, idx: int, pct: int, message: str) -> None:
        # Compose the intra-file fraction into the whole-run bar so long files
        # show live movement, not a bar frozen per file.
        self.progress_widget.set_composed(idx, pct, self._item_total(), message)

    def _on_file_finished(self, idx: int, out_path: object, error_str: object) -> None:
        # Whole-file advance in the same percent unit system as set_composed
        # (a count-unit set_progress here would flip the ETA denominator).
        total = self._item_total()
        if total:
            self.progress_widget.set_percent(int((idx + 1) / total * 100))
        if error_str:
            self.log_widget.append_error(str(error_str))
        else:
            path_label = str(out_path) if out_path else ""
            self.log_widget.append_success(
                self._strings.done_prefix + Path(path_label).name if path_label else self._strings.done
            )

    def _on_file_skipped(self, idx: int, out_path: object) -> None:
        # Advance the progress bar just like a finished file.
        total = self._item_total()
        if total:
            self.progress_widget.set_percent(int((idx + 1) / total * 100))
        path_label = str(out_path) if out_path else ""
        self.log_widget.append_info(
            self._strings.skipped_prefix + Path(path_label).name if path_label else self._strings.skipped
        )

    def _on_queue_finished(self) -> None:
        self._primary_button.setEnabled(True)
        self.cancel_button.hide()
        # Reset for the next run's cancel button.
        self.cancel_button.setText(self._strings.cancel)
        self.cancel_button.setEnabled(True)
        if self._cancelled:
            self.progress_widget.reset()
            self.progress_widget.set_status(self._strings.cancelled)
        else:
            self.progress_widget.show_completion(tr_format(self._strings.complete_template, self._item_total()))

    def _on_worker_finished(self) -> None:
        """Release the QThread once it has actually exited."""
        worker = self.worker_thread
        if worker is not None:
            worker.deleteLater()
            self.worker_thread = None

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _on_cancel(self) -> None:
        self._cancelled = True
        if self.worker_thread is not None:
            self.worker_thread.cancel()
        self.cancel_button.setText(self._strings.cancelling)
        self.cancel_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(self) -> Iterator[CancellableWorker]:
        """Yield the active worker so BackgroundTaskController can join it on close."""
        if self.worker_thread is not None:
            yield self.worker_thread

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _item_total(self) -> int:
        """Return the total item count for this run (files or pairs)."""
        raise NotImplementedError

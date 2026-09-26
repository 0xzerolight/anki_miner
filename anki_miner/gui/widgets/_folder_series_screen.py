"""Shared base for folder-pair season screens, lifted out of ``BatchProcessingTab``.

A folder-series screen mines a season from a folder pair (video + subtitle,
plus an optional secondary-subtitle translation folder) rather than a single
episode. ``BatchProcessingTab`` is today's only subclass; a screen that mines
the same folder-pair trio through a different worker pipeline (Deck Builder)
subclasses this too, so this base holds only the parts that do not vary
between them: the translation-folder gate/validation/offset, drag-and-drop
folder routing, the per-episode progress slots (status line only — the bar
counts whole items, not episodes within one), the curation dialog's media
context, and the run's terminal progress line. Subclasses build their own Add
Series card, own worker, and own ``_on_queue_finished``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from anki_miner.gui.utils.qt_helpers import urls_from_event
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.base import ScreenIssue
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
from anki_miner.gui.widgets.enhanced import FileSelector
from anki_miner.gui.widgets.progress_widget import ProgressWidget
from anki_miner.utils.file_pairing import is_same_folder
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread


class FolderSeriesScreenBase(MiningTabBase):
    """Common scaffolding for folder-pair season screens.

    Subclasses own their layout, their Add Series card, their worker, and the
    rest of their terminal-run handling. This base provides the pieces that
    are identical across every such screen (see the module docstring).
    """

    # The Add Series card's folder pickers, built by every subclass. Bare
    # annotations only — see ``MiningTabBase.config`` — so the methods below
    # can read them without a per-call type: ignore. The offset rows come from
    # ``MiningTabBase._build_offset_rows``.
    video_folder_selector: FileSelector
    subtitle_folder_selector: FileSelector
    secondary_folder_selector: FileSelector

    # Set by every subclass's ``__init__`` (``BatchProcessingTab`` today).
    # Declared here, bare, for the same reason.
    worker_thread: BatchQueueWorkerThread | None

    def _apply_secondary_gate(self) -> None:
        """Show the translation folder and its offset only when the setting is on."""
        enabled = self.config.secondary_subtitle_enabled
        self.secondary_folder_selector.setVisible(enabled)
        self.secondary_offset_row.setVisible(enabled)

    def _validated_secondary_folder(self, subtitle_folder: Path) -> tuple[bool, Path | None]:
        """The Add Series card's translation folder as ``(ok, folder)``.

        Kept apart from :meth:`_get_validated_folders` because the two answer
        different questions: the video/subtitle pair is required, this one is
        optional and may simply be absent. ``ok`` is False when the picker
        holds a path that has since gone, or the subtitle folder itself --
        the matcher refuses that silently, and silently mining without the
        translations the user chose is worse than saying so.
        """
        if not self.config.secondary_subtitle_enabled:
            return True, None
        secondary_path = self.secondary_folder_selector.path_or_none()
        if secondary_path is None:
            return True, None
        if not self.secondary_folder_selector.is_valid():
            self.show_screen_issue(
                ScreenIssue(
                    summary=QCoreApplication.translate(
                        "BatchProcessingTab", "That translation subtitle folder no longer exists."
                    ),
                    details=secondary_path,
                )
            )
            return False, None
        secondary_folder = Path(secondary_path)
        if is_same_folder(secondary_folder, subtitle_folder):
            self.show_screen_issue(
                ScreenIssue(
                    summary=QCoreApplication.translate(
                        "BatchProcessingTab", "The translation folder must be different from the subtitle folder."
                    )
                )
            )
            return False, None
        return True, secondary_folder

    def _secondary_offset(self) -> float:
        """The Add Series card's translation offset, or 0.0 without a chosen folder."""
        if not self.config.secondary_subtitle_enabled:
            return 0.0
        if self.secondary_folder_selector.path_or_none() is None:
            return 0.0
        return self.secondary_offset_spinbox.value()

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Accept drag if any URL is a directory."""
        if event is None:
            return
        for url in urls_from_event(event):
            if Path(url.toLocalFile()).is_dir():
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Route dropped folders to the appropriate folder selector."""
        if event is None:
            return
        folders = [url.toLocalFile() for url in urls_from_event(event) if Path(url.toLocalFile()).is_dir()]
        if len(folders) >= 1:
            self.video_folder_selector.set_path(folders[0])
        if len(folders) >= 2:
            self.subtitle_folder_selector.set_path(folders[1])
        event.acceptProposedAction()

    def _on_progress_stage(self, index: int, total: int, name: str) -> None:
        """Per-episode stage: status only.

        Unlike a single-episode run, the bar here counts episodes/series, so a
        stage inside one of them must not move it — that is exactly the blend
        that made a long episode look like a stalled batch.
        """
        self._stage_line.on_stage(index, total, name)
        self._publish_task_stage(index, total, name)

    def _on_progress_start(self, total: int, description: str) -> None:
        """Per-episode stage start: status only (the bar counts whole items).

        Args:
            total: Items in this stage (used for the true count in the label)
            description: Stage description
        """
        self._stage_line.on_start(total, description)

    def _on_progress_update(self, current: int, item_description: str) -> None:
        """Per-episode within-stage progress: status label only.

        Args:
            current: True item number inside the current stage
            item_description: Stage/item detail
        """
        self._stage_line.on_progress(current, item_description)

    def _on_progress_complete(self) -> None:
        """Per-episode stage complete: no-op (terminal handlers own the summary)."""

    def _build_curation_context(
        self,
    ) -> tuple[CurationMediaContext | None, Callable[[str], list[tuple[str, str]]] | None]:
        """Build (media_context, lookup_fn) from the live worker's current pair.

        The worker is blocked in ``_curation_event.wait()`` while this runs, so
        reading its ``_curation_*`` attributes is race-free.
        """
        w = self.worker_thread
        if w is None:
            return None, None
        media_context = self._make_curation_media_context(
            self.config,
            w._curation_video,
            w._curation_subtitle,
            offset=w._curation_offset,
            secondary_subtitle=w._curation_secondary,
            secondary_offset=w._curation_secondary_offset,
        )
        season_map = getattr(w, "_curation_media_map", None)
        if media_context is not None and season_map:
            # Season curation: give the dialog a resolver over a SNAPSHOT of
            # the worker's episode map, so cross-episode word focus can rebuild
            # the player context without ever touching the worker after it
            # unparks. _make_curation_media_context is static, pure and
            # error-swallowing, so the resolver is safe off the GUI thread.
            snapshot = dict(season_map)
            config = self.config

            def _resolve(video: Path) -> CurationMediaContext | None:
                episode = snapshot.get(video)
                if episode is None:
                    return None
                return MiningTabBase._make_curation_media_context(
                    config,
                    video,
                    episode.subtitle,
                    offset=episode.offset,
                    secondary_subtitle=episode.secondary,
                    secondary_offset=episode.secondary_offset,
                )

            media_context = replace(media_context, context_resolver=_resolve)
        return media_context, self._lookup_fn_from_processor(w.curation_processor)

    def _show_terminal_progress(self, widget: ProgressWidget, total_cards: int) -> None:
        """Write the run's terminal end state onto ``widget``.

        Terminal end state: cancel -> failed -> success. A cancelled run keeps
        its frozen bar; only a fatal failure clears it.
        """
        if getattr(self, "_cancel_requested", False):
            widget.set_status(QCoreApplication.translate("BatchProcessingTab", "Cancelled"))
        elif getattr(self, "_run_failed", False):
            widget.reset()
            widget.set_status(QCoreApplication.translate("BatchProcessingTab", "Failed — see log"))
        elif getattr(self, "_run_had_item_failures", False):
            widget.reset()
            widget.set_status(QCoreApplication.translate("BatchProcessingTab", "Finished with errors — see log"))
        else:
            widget.show_completion(
                tr_format(QCoreApplication.translate("BatchProcessingTab", "Complete — %1 cards created"), total_cards)
            )

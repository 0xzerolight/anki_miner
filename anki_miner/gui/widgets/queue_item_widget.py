"""One Batch queue row: series name, episode count, state word, cards (D2, A02).

Batch rows used to be tall cards with folder paths, a status pill and their own
Edit and Remove buttons, so two series already overflowed the list. D31 gave the
YouTube and Audiobook queues one calm line per row; this is the same line for a
series. The folders move to the tooltip, Edit moves to the selection bar (and a
double-click), and Remove is a selection action like on the other queues.

The widget keeps the public API the panel and the tab already call
(``set_folders``, ``set_status``, ``set_episode_count`` and so on); only what it
paints changed.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QWidget

from anki_miner.gui.widgets.base.queue_row import QueueRowWidget, state_word
from anki_miner.utils.i18n import tr_format

#: Row status -> filter bucket: the same four words the chips print, so the
#: Failed chip selects exactly the rows reading "Failed".
STATUS_BUCKETS: dict[str, str] = {
    "pending": "ready",
    "processing": "running",
    "error": "failed",
    "complete": "complete",
}


class QueueItemWidget(QueueRowWidget):
    """One series in the Batch queue, rendered as a single line (D2)."""

    def __init__(self, display_name: str = "", parent: QWidget | None = None) -> None:
        """Build the row.

        Args:
            display_name: The series name shown as the row's title.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setObjectName("batch-queue-item")
        self.display_name = display_name or "Untitled Series"
        # Stable identity stamped with the BatchQueue QueueItem.id when the row
        # binds, so worker updates address the right row even when two rows
        # share a display_name (T-30).
        self.item_id = ""
        self._status = "pending"
        self._video_folder = ""
        self._subtitle_folder = ""
        self._episode_count = 0
        self._cards_created = 0
        self._subtitle_offset = 0.0
        self._secondary_folder = ""
        self._secondary_offset = 0.0
        self._render()

    # ------------------------------------------------------------------
    # Model
    # ------------------------------------------------------------------

    def set_folders(self, video_folder: Path, subtitle_folder: Path, secondary_folder: Path | None = None) -> None:
        """Set the row's folders (the translation folder is optional, F7)."""
        self._video_folder = str(video_folder)
        self._subtitle_folder = str(subtitle_folder)
        self._secondary_folder = str(secondary_folder) if secondary_folder else ""
        self._render()

    def get_folders(self) -> tuple[Path | None, Path | None]:
        """Return ``(video_folder, subtitle_folder)``, each ``None`` when unset."""
        video = Path(self._video_folder) if self._video_folder else None
        subtitle = Path(self._subtitle_folder) if self._subtitle_folder else None
        return (video, subtitle)

    def get_status(self) -> str:
        """Return the status: 'pending', 'processing', 'complete' or 'error'."""
        return self._status

    def set_status(self, status: str) -> None:
        """Set the status: 'pending', 'processing', 'complete' or 'error'."""
        self._status = status
        self._render()

    def set_episode_count(self, count: int) -> None:
        """Set how many episode pairs the folders hold (A02)."""
        self._episode_count = count
        self._render()

    def get_episode_count(self) -> int:
        """Return the episode count, or 0 when not known yet."""
        return self._episode_count

    def set_cards_created(self, count: int) -> None:
        """Set how many cards this series produced."""
        self._cards_created = count
        self._render()

    def get_cards_created(self) -> int:
        """Return the cards this series produced."""
        return self._cards_created

    @property
    def subtitle_offset(self) -> float:
        """The per-series subtitle offset, in seconds."""
        return self._subtitle_offset

    @subtitle_offset.setter
    def subtitle_offset(self, value: float) -> None:
        self._subtitle_offset = value
        self._render()

    @property
    def secondary_folder(self) -> Path | None:
        """The translation-subtitle folder (F7), or ``None``."""
        return Path(self._secondary_folder) if self._secondary_folder else None

    @secondary_folder.setter
    def secondary_folder(self, value: Path | None) -> None:
        self._secondary_folder = str(value) if value else ""
        self._render()

    @property
    def secondary_offset(self) -> float:
        """Offset applied to this row's translation subtitles, in seconds."""
        return self._secondary_offset

    @secondary_offset.setter
    def secondary_offset(self, value: float) -> None:
        self._secondary_offset = value

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self) -> None:
        """Paint the one line: name, episodes, state word, cards when done."""
        self.render_row(
            title=self.display_name,
            aside=self._episodes_text(),
            state=state_word(STATUS_BUCKETS.get(self._status, "ready")),
            result=tr_format(self.tr("Cards: %1"), self._cards_created) if self._status == "complete" else "",
            detail=self._tooltip_text(),
        )

    def _episodes_text(self) -> str:
        """The aside: "3 episodes", "1 episode", or nothing before the count is known.

        ``%n``, not a 1-or-other split: each catalog carries its own plural forms
        (Russian has three).
        """
        if self._episode_count <= 0:
            return ""
        return self.tr("%n episode(s)", "", self._episode_count)

    def _tooltip_text(self) -> str:
        """Everything that no longer fits on the row: folders, offset, how to edit."""
        lines: list[str] = []
        if self._video_folder:
            lines.append(tr_format(self.tr("Video folder: %1"), self._video_folder))
        if self._subtitle_folder:
            lines.append(tr_format(self.tr("Subtitle folder: %1"), self._subtitle_folder))
        if self._secondary_folder:
            lines.append(tr_format(self.tr("Translation folder: %1"), self._secondary_folder))
        if self._subtitle_offset != 0.0:
            sign = "+" if self._subtitle_offset > 0 else ""
            lines.append(tr_format(self.tr("Offset: %1"), f"{sign}{self._subtitle_offset:.1f}s"))
        lines.append(self.tr("Double-click to edit"))
        return "\n".join(lines)

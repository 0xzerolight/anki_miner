"""Pre-run metadata editor for condensed audio outputs (Issue #113)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.gui.widgets.enhanced.file_selector import FileSelector
from anki_miner.gui.widgets.enhanced.modern_button import ModernButton
from anki_miner.services.audio_tagger import TrackMetadata

_PREVIEW_EDGE = 96
_IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.webp)"


class CondenseMetadataDialog(QDialog):
    """Editable per-file tag table + global genre/artist/artwork fields.

    Dumb view: the caller computes the pre-fill (``prefill_track_metadata``)
    and applies the result; Cancel aborts the condense run at the call site.
    ``metadata()`` is meaningful only after ``exec()`` returned Accepted.
    """

    COL_FILE, COL_TRACK, COL_TITLE, COL_ALBUM, COL_ARTIST = range(5)

    def __init__(
        self,
        filenames: list[str],
        prefill: list[TrackMetadata],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Condensed Audio Metadata"))
        add_min_max_buttons(self)
        layout = QVBoxLayout(self)

        # --- global fields -------------------------------------------------
        artist_row = QHBoxLayout()
        artist_row.addWidget(QLabel(self.tr("Artist:")))
        self.artist_edit = QLineEdit()
        artist_row.addWidget(self.artist_edit)
        self.apply_artist_button = ModernButton(self.tr("Apply to all rows"), variant="secondary")
        self.apply_artist_button.clicked.connect(self._apply_artist_to_all)
        artist_row.addWidget(self.apply_artist_button)
        layout.addLayout(artist_row)

        genre_row = QHBoxLayout()
        genre_row.addWidget(QLabel(self.tr("Genre:")))
        self.genre_edit = QLineEdit(self.tr("Condensed Audio"))
        genre_row.addWidget(self.genre_edit)
        genre_row.addStretch()
        layout.addLayout(genre_row)

        artwork_row = QHBoxLayout()
        self.artwork_selector = FileSelector(
            label=self.tr("Artwork:"),
            file_mode=True,
            file_filter=_IMAGE_FILTER,
            optional=True,
        )
        self.artwork_selector.path_changed.connect(self._update_artwork_preview)
        artwork_row.addWidget(self.artwork_selector, stretch=1)
        self.artwork_preview = QLabel()
        self.artwork_preview.setFixedSize(_PREVIEW_EDGE, _PREVIEW_EDGE)
        self.artwork_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        artwork_row.addWidget(self.artwork_preview)
        layout.addLayout(artwork_row)

        # --- per-file table ------------------------------------------------
        self.table = QTableWidget(len(filenames), 5, self)
        self.table.setHorizontalHeaderLabels(
            [self.tr("File"), self.tr("Track #"), self.tr("Title"), self.tr("Album"), self.tr("Artist")]
        )
        vertical_header = self.table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)
        for row, (name, meta) in enumerate(zip(filenames, prefill, strict=True)):
            file_item = QTableWidgetItem(name)
            file_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # read-only
            self.table.setItem(row, self.COL_FILE, file_item)
            track_text = "" if meta.track is None else str(meta.track)
            self.table.setItem(row, self.COL_TRACK, QTableWidgetItem(track_text))
            self.table.setItem(row, self.COL_TITLE, QTableWidgetItem(meta.title))
            self.table.setItem(row, self.COL_ALBUM, QTableWidgetItem(meta.album))
            self.table.setItem(row, self.COL_ARTIST, QTableWidgetItem(meta.artist))
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def metadata(self) -> list[TrackMetadata]:
        """Fold table rows + global genre/artwork into per-file metadata."""
        genre = self.genre_edit.text().strip()
        artwork = self._artwork_path()
        result: list[TrackMetadata] = []
        for row in range(self.table.rowCount()):
            result.append(
                TrackMetadata(
                    title=self._cell(row, self.COL_TITLE),
                    album=self._cell(row, self.COL_ALBUM),
                    artist=self._cell(row, self.COL_ARTIST),
                    track=self._parse_track(self._cell(row, self.COL_TRACK)),
                    genre=genre,
                    artwork_path=artwork,
                )
            )
        return result

    def _cell(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text().strip() if item is not None else ""

    @staticmethod
    def _parse_track(text: str) -> int | None:
        try:
            return int(text)
        except ValueError:
            return None

    def _apply_artist_to_all(self) -> None:
        artist = self.artist_edit.text().strip()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_ARTIST)
            if item is not None:
                item.setText(artist)

    def _artwork_path(self) -> Path | None:
        raw = self.artwork_selector.get_path()
        if not raw:
            return None
        path = Path(raw)
        return path if path.is_file() else None

    def _update_artwork_preview(self, raw: str) -> None:
        pixmap = QPixmap(raw) if raw else QPixmap()
        if pixmap.isNull():
            self.artwork_preview.clear()
            return
        self.artwork_preview.setPixmap(
            pixmap.scaled(
                _PREVIEW_EDGE,
                _PREVIEW_EDGE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

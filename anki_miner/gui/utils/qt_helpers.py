"""Typed Qt helpers that absorb Optional-returning accessors with documented invariants."""

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QDialog, QHeaderView, QTableWidget


def urls_from_event(event: QDropEvent | QDragEnterEvent) -> list[QUrl]:
    """Return URLs from a drag/drop event. Returns [] if mimeData is unavailable.

    Qt stub returns Optional[QMimeData] but runtime always populates it for
    drag-drop events; we treat None as "no URLs" to keep callers branch-free.
    Previously, callers with a None guard would AttributeError if mimeData()
    somehow returned None; this helper degrades gracefully to a no-op drop instead.
    """
    mime = event.mimeData()
    if mime is None:
        return []
    return list(mime.urls())


def configure_table_header(
    table: QTableWidget,
    resize_mode: QHeaderView.ResizeMode = QHeaderView.ResizeMode.Stretch,
) -> None:
    """Configure stretch and resize mode on a table's horizontal header.

    Qt stub returns Optional[QHeaderView] for `horizontalHeader()` but it is
    always present on a constructed QTableWidget at runtime.
    """
    header = table.horizontalHeader()
    if header is not None:
        header.setStretchLastSection(True)
        header.setSectionResizeMode(resize_mode)


def add_min_max_buttons(dialog: QDialog) -> None:
    """Add minimize/maximize title-bar buttons to a resizable dialog.

    Windows gives a plain ``QDialog`` only a close button; OR in the min/max
    hints so resizable dialogs behave like normal windows. Linux WMs already
    show all three regardless of flags. Call before the dialog is first shown:
    ``setWindowFlags`` re-parents and hides an already-visible widget (PyQt6),
    so callers invoke this during ``__init__``, ahead of ``exec()``.

    OR-ing onto ``windowFlags()`` preserves the existing close/system-menu hints
    and the implicit ``exec()`` modality; only the two button hints are added.
    """
    dialog.setWindowFlags(
        dialog.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint
    )

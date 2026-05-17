"""Typed Qt helpers that absorb Optional-returning accessors with documented invariants."""

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QHeaderView, QTableWidget


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

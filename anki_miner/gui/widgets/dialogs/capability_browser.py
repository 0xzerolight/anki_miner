"""The "Find a Feature" browser (Tools menu).

A searchable catalogue of everything Anki Miner can do, built to answer the
recurring "Can it do X?" support question. Each row names a feature, describes it
in one line, and offers an "Open" button that jumps to where the feature lives.

The dialog itself only *records* the chosen target; :func:`run_capability_browser`
performs the navigation after the modal closes (close-then-navigate), so the tab
switch is never hidden behind the still-open dialog and the widget stays trivially
testable without a live main window.
"""

from __future__ import annotations

from typing import Protocol

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.capabilities import (
    TRANSLATION_CONTEXT,
    Capability,
    CapabilityTarget,
    search,
)
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons


class _RevealTarget(Protocol):
    """Minimal surface the browser needs from the main window."""

    def reveal_capability(self, target: CapabilityTarget) -> None: ...


def _tr(text: str) -> str:
    """Localise a registry string under the shared Capabilities context."""
    return QCoreApplication.translate(TRANSLATION_CONTEXT, text)


class CapabilityBrowser(QDialog):
    """Search box over a scrollable, category-grouped feature list."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Find a Feature"))
        self.setObjectName("capability-browser")
        self.resize(560, 600)

        # Set when the user clicks an "Open" button; read by run_capability_browser.
        self.selected_target: CapabilityTarget | None = None
        # Currently displayed (filtered) capabilities, in registry order.
        self._current: list[Capability] = []

        layout = QVBoxLayout(self)

        intro = QLabel(_tr("Search every Anki Miner feature."))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("capability-search")
        self.search_box.setPlaceholderText(_tr('Search features, e.g. "i+1", "pitch", "youtube"'))
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_box)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.addStretch()  # keeps rows top-aligned
        self._scroll.setWidget(self._list_container)
        layout.addWidget(self._scroll, stretch=1)

        self._empty_label = QLabel(_tr("No matching features."))
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()
        layout.addWidget(self._empty_label)

        self._apply_filter("")
        self.search_box.setFocus()
        add_min_max_buttons(self)

    # ------------------------------------------------------------------ filter
    def visible_capabilities(self) -> list[Capability]:
        """The capabilities currently shown (after the active search filter)."""
        return list(self._current)

    def _apply_filter(self, text: str) -> None:
        self._current = search(text)
        self._rebuild_rows()

    def _clear_rows(self) -> None:
        # Remove every widget but keep the trailing stretch (last item).
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # setParent(None) detaches synchronously so findChildren() (and
                # the next rebuild) never sees a stale row; deleteLater frees it.
                widget.setParent(None)
                widget.deleteLater()

    def _rebuild_rows(self) -> None:
        self._clear_rows()
        self._empty_label.setVisible(not self._current)
        last_category: str | None = None
        for cap in self._current:
            if cap.category != last_category:
                self._list_layout.insertWidget(self._list_layout.count() - 1, self._make_header(cap.category))
                last_category = cap.category
            self._list_layout.insertWidget(self._list_layout.count() - 1, self._make_row(cap))

    # ------------------------------------------------------------------ widgets
    def _make_header(self, category: str) -> QLabel:
        header = QLabel(_tr(category))
        header.setObjectName("capability-category")
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        return header

    def _make_row(self, cap: Capability) -> QFrame:
        row = QFrame()
        row.setObjectName("capability-row")
        row.setFrameShape(QFrame.Shape.StyledPanel)
        row_layout = QHBoxLayout(row)

        text_col = QVBoxLayout()
        title = QLabel(_tr(cap.title))
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        desc = QLabel(_tr(cap.description))
        desc.setWordWrap(True)
        desc.setObjectName("capability-description")
        text_col.addWidget(title)
        text_col.addWidget(desc)
        row_layout.addLayout(text_col, stretch=1)

        open_button = QPushButton(_tr("Open ▸"))
        open_button.setObjectName("capability-open")
        # default=False so Enter in the search box doesn't fire a random row.
        open_button.setAutoDefault(False)
        open_button.clicked.connect(lambda _checked=False, c=cap: self._choose(c))
        row_layout.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignTop)
        return row

    def _choose(self, cap: Capability) -> None:
        self.selected_target = cap.target
        self.accept()


def run_capability_browser(parent: QWidget | None, main_window: _RevealTarget) -> None:
    """Show the browser modally, then navigate to the chosen feature (if any).

    ``parent`` is the Qt parent for centering; ``main_window`` receives the
    navigation via :meth:`reveal_capability`. Navigation happens only after the
    dialog closes so the tab switch is visible.
    """
    dialog = CapabilityBrowser(parent)
    dialog.exec()
    target = dialog.selected_target
    if target is not None:
        main_window.reveal_capability(target)

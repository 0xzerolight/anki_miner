"""The Usage Guide browser (menu-bar button, F1).

A searchable catalogue of everything Anki Miner can do, built to answer the
recurring "Can it do X?" support question. It is one list (E11): each row names
a feature and describes it in a line or two; category rows group them. One Open
button in the footer, a double-click or Enter on a row goes to where the
feature lives. Menu/dialog-only entries carry no target: Open stays disabled on
them and their description says where the feature lives instead. Enter in the
search box only searches (D49-B), so nothing here is a default button.

The dialog itself only *records* the chosen target; :func:`run_capability_browser`
performs the navigation after the modal closes (close-then-navigate), so the tab
switch is never hidden behind the still-open dialog and the widget stays trivially
testable without a live main window.
"""

from __future__ import annotations

from typing import Protocol

from PyQt6.QtCore import QCoreApplication, QEvent, QModelIndex, QObject, QRect, QSize, Qt
from PyQt6.QtGui import QFont, QFontMetrics, QKeyEvent, QPainter, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from anki_miner.gui.capabilities import (
    TRANSLATION_CONTEXT,
    Capability,
    CapabilityTarget,
    search,
)
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.base import EnhancedDialog

#: Item role carrying a row's translated description (the title is DisplayRole).
_DESCRIPTION_ROLE = Qt.ItemDataRole.UserRole + 1


class _RevealTarget(Protocol):
    """Minimal surface the browser needs from the main window."""

    def reveal_capability(self, target: CapabilityTarget) -> None: ...


def _tr(text: str) -> str:
    """Localise a registry value already declared with ``QT_TRANSLATE_NOOP``.

    Only for category/title/description values registered in
    ``gui/capabilities.py``. Never call this with a string literal here:
    ``pylupdate6`` cannot see through the wrapper, so a literal argument would
    never reach the catalogues. Use ``QCoreApplication.translate("Capabilities",
    "...")`` directly for those instead.
    """
    return QCoreApplication.translate(TRANSLATION_CONTEXT, text)


class _CapabilityDelegate(QStyledItemDelegate):
    """Paints a row as a bold title over its wrapped description.

    Category rows have no description and paint as a bold caption. The
    background (hover, selection) is the stylesheet's ``QListWidget::item``
    look, drawn by the style before the text goes on top.
    """

    _PAD = SPACING.xs

    def _width(self, option: QStyleOptionViewItem) -> int:
        view = option.widget
        if isinstance(view, QAbstractItemView) and view.viewport() is not None:
            viewport = view.viewport()
            assert viewport is not None
            return max(1, viewport.width() - 2 * self._PAD)
        return max(1, option.rect.width() - 2 * self._PAD)

    @staticmethod
    def _fonts(option: QStyleOptionViewItem) -> tuple[QFont, QFont]:
        title = QFont(option.font)
        title.setBold(True)
        return title, QFont(option.font)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802 - Qt override
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        description = str(index.data(_DESCRIPTION_ROLE) or "")
        title_font, description_font = self._fonts(opt)
        width = self._width(opt)
        flags = int(Qt.TextFlag.TextWordWrap)
        height = QFontMetrics(title_font).boundingRect(QRect(0, 0, width, 100_000), flags, title).height()
        if description:
            height += (
                SPACING.xxs
                + QFontMetrics(description_font).boundingRect(QRect(0, 0, width, 100_000), flags, description).height()
            )
        return QSize(width + 2 * self._PAD, height + 2 * self._PAD)

    def paint(self, painter: QPainter | None, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if painter is None:
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        description = str(index.data(_DESCRIPTION_ROLE) or "")
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        title_font, description_font = self._fonts(opt)
        rect = opt.rect.adjusted(self._PAD, self._PAD, -self._PAD, -self._PAD)
        flags = int(Qt.TextFlag.TextWordWrap)
        painter.save()
        painter.setPen(opt.palette.color(role))
        painter.setFont(title_font)
        title_rect = painter.boundingRect(rect, flags, title)
        painter.drawText(rect, flags, title)
        if description:
            painter.setFont(description_font)
            painter.drawText(rect.adjusted(0, title_rect.height() + SPACING.xxs, 0, 0), flags, description)
        painter.restore()


class CapabilityBrowser(EnhancedDialog):
    """Search box over one category-grouped feature list, with one Open button."""

    def __init__(self, parent: QWidget | None = None, capabilities: frozenset[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(QCoreApplication.translate("Capabilities", "Anki Miner Usage Guide"))
        self.setObjectName("capability-browser")
        # E11: it could be shrunk to 94x129, which showed nothing usable.
        self.setMinimumSize(480, 420)
        self.resize(560, 600)
        self.set_header(
            "",
            QCoreApplication.translate("Capabilities", "Usage Guide"),
            QCoreApplication.translate("Capabilities", "Everything Anki Miner can do, and where to find it."),
        )

        # The active mining language's profile capabilities, passed to every
        # search so a gated entry never reaches a language that cannot use it.
        # None (no language in scope) lists the whole catalogue.
        self._capabilities = capabilities
        # Set when the user opens a row; read by run_capability_browser.
        self.selected_target: CapabilityTarget | None = None
        # Currently displayed (filtered) capabilities, in registry order.
        self._current: list[Capability] = []

        self.search_box = QLineEdit()
        self.search_box.setObjectName("capability-search")
        self.search_box.setPlaceholderText(
            QCoreApplication.translate("Capabilities", 'Search features, e.g. "i+1", "pitch", "youtube"')
        )
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._apply_filter)
        self.add_content(self.search_box)

        self.list = QListWidget()
        self.list.setObjectName("capability-list")
        self.list.setItemDelegate(_CapabilityDelegate(self.list))
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(False)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemDoubleClicked.connect(self._open_item)
        self.list.currentItemChanged.connect(self._sync_open_button)
        self.list.installEventFilter(self)
        self.add_content(self.list, 1)

        self._empty_label = QLabel(QCoreApplication.translate("Capabilities", "No matching features."))
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()
        self.add_content(self._empty_label)

        # Neither footer button may become Enter's target (D49-B): only the
        # primary variant auto-defaults, and neither is primary.
        self.open_button = self.add_button(
            QCoreApplication.translate("Capabilities", "Open"), "secondary", self._open_selected
        )
        self.open_button.setAutoDefault(False)
        self.open_button.setEnabled(False)
        self.close_button = self.add_button(
            QCoreApplication.translate("Capabilities", "Close"), "secondary", self.reject
        )
        self.close_button.setAutoDefault(False)

        self._apply_filter("")
        self.search_box.setFocus()

    # ------------------------------------------------------------------ filter
    def _apply_filter(self, text: str) -> None:
        self._current = search(text, self._capabilities)
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        self.list.clear()
        self._empty_label.setVisible(not self._current)
        self.list.setVisible(bool(self._current))
        last_category: str | None = None
        for cap in self._current:
            if cap.category != last_category:
                header = QListWidgetItem(_tr(cap.category))
                header.setData(Qt.ItemDataRole.UserRole, None)
                header.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.list.addItem(header)
                last_category = cap.category
            item = QListWidgetItem(_tr(cap.title))
            item.setData(Qt.ItemDataRole.UserRole, cap.id)
            item.setData(_DESCRIPTION_ROLE, _tr(cap.description))
            item.setToolTip(_tr(cap.description))
            self.list.addItem(item)
        self._sync_open_button()

    # ------------------------------------------------------------------ opening
    def _capability(self, item: QListWidgetItem | None) -> Capability | None:
        if item is None:
            return None
        cap_id = item.data(Qt.ItemDataRole.UserRole)
        return next((cap for cap in self._current if cap.id == cap_id), None)

    def _sync_open_button(self, *_: object) -> None:
        cap = self._capability(self.list.currentItem())
        self.open_button.setEnabled(cap is not None and cap.target is not None)

    def _open_item(self, item: QListWidgetItem | None) -> None:
        cap = self._capability(item)
        if cap is not None and cap.target is not None:
            self._choose(cap)

    def _open_selected(self) -> None:
        self._open_item(self.list.currentItem())

    def _choose(self, cap: Capability) -> None:
        self.selected_target = cap.target
        self.accept()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:  # noqa: N802 - Qt override
        """Enter on the list opens the current row; Enter in the search box does not."""
        if (
            a0 is self.list
            and isinstance(a1, QKeyEvent)
            and a1.type() == QEvent.Type.KeyPress
            and a1.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self._open_selected()
            return True
        return super().eventFilter(a0, a1)


def run_capability_browser(
    parent: QWidget | None,
    main_window: _RevealTarget,
    capabilities: frozenset[str] | None = None,
) -> None:
    """Show the browser modally, then navigate to the chosen feature (if any).

    ``parent`` is the Qt parent for centering; ``main_window`` receives the
    navigation via :meth:`reveal_capability`. Navigation happens only after the
    dialog closes so the tab switch is visible. ``capabilities`` is the active
    mining language's profile capability set, which gates the listed entries.
    """
    dialog = CapabilityBrowser(parent, capabilities)
    dialog.exec()
    target = dialog.selected_target
    if target is not None:
        main_window.reveal_capability(target)

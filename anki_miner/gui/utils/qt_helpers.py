"""Typed Qt helpers, and the one data-surface contract every view is built through.

Two groups live here. The first absorbs Optional-returning Qt accessors whose
invariants the app can document. The second (decision D42) is the shared
configuration for **every** table, list and tree: alignment, tabular figures,
row height, selection, copy and typed sorting.

Use the D42 helpers rather than configuring a view by hand. Before them, a
"data surface" meant whatever each screen happened to write: Analytics centred
its dates and its counts alike, drew a row-number column and stretched six
columns to equal width, while lists took the OS selection colour and tables took
the theme's -- two different-looking selections on one screen. The row height
came from a different formula in each file, and numbers sorted as text, so 100
ranked above 20.
"""

from collections.abc import Callable, Iterable, Sequence
from enum import Enum

from PyQt6.QtCore import QEvent, QModelIndex, QObject, QSize, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QHeaderView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.keyboard_shortcuts import scoped_shortcut

#: The value a cell sorts by, as opposed to the string it prints. Sits above
#: ``UserRole`` because column 0 of the word curator already stores its
#: original-word index there, and D42 must not overwrite it.
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
#: The full, untruncated value a cell copies. The display text is elided.
COPY_ROLE = Qt.ItemDataRole.UserRole + 2

#: The padding common.qss gives every data cell on its left and right edges.
#: Column widths are measured *including* it: a column sized to the text alone is
#: a column that clips its own text once the stylesheet adds the padding back.
CELL_PADDING = SPACING.xs
#: The same, above and below (D40). Vertical is the scarce axis — a row only has
#: to separate itself from the row above, so it spends the smallest step there
#: and buys back a quarter of the rows in every table, list and tree. Whatever
#: this is, ``QTableWidget::item``'s vertical padding in ``common.qss`` must
#: match it, or the taller of the two wins and the shorter one clips.
CELL_PADDING_Y = SPACING.xxs


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


def reveal_settings(origin: QWidget, subtab: str) -> None:
    """Take the user to a Settings destination from wherever ``origin`` lives.

    The repair inside a screen-issue banner ("Open Media Settings") has to get
    somewhere, and a tab does not hold a reference to the window that owns it.
    Resolved by duck typing against the top-level window's ``reveal_capability``
    — the same self-healing, stable-key lookup the Find a Feature browser uses,
    so no screen ever learns a tab index. A window without it (a bare widget in
    a test) is a no-op, not a crash.
    """
    from anki_miner.gui.capabilities import CapabilityTarget

    reveal = getattr(origin.window(), "reveal_capability", None)
    if callable(reveal):
        reveal(CapabilityTarget("settings", subtab))


def configure_table_header(
    table: QTableWidget,
    resize_mode: QHeaderView.ResizeMode = QHeaderView.ResizeMode.Stretch,
    *,
    fit_columns: Iterable[int] = (),
) -> None:
    """Configure stretch and resize mode on a table's horizontal header.

    ``fit_columns`` size to their content instead of taking ``resize_mode``. A
    date or a count needs one width and keeps it; only prose columns should
    absorb the leftover pixels. Stretching all six Analytics columns equally is
    what left episode titles elided beside a three-digit card count with room to
    spare.

    Passing any ``fit_columns`` also stops the header stretching its last
    section: a mixed header already has a stretching column to absorb leftover
    width, and stretch-last would otherwise override a trailing fitted column.

    Qt stub returns Optional[QHeaderView] for `horizontalHeader()` but it is
    always present on a constructed QTableWidget at runtime.

    Args:
        table: The table whose horizontal header is configured.
        resize_mode: Mode applied to every column not named in ``fit_columns``.
        fit_columns: Columns that size to their own content.
    """
    header = table.horizontalHeader()
    if header is None:
        return
    fitted = tuple(fit_columns)
    header.setStretchLastSection(not fitted)
    header.setSectionResizeMode(resize_mode)
    for column in fitted:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)


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


class _NoScrollEventFilter(QObject):
    """Swallow wheel events on unfocused spin/combo widgets (Issue #99).

    In the settings scroll areas a wheel event over a ``QAbstractSpinBox`` /
    ``QComboBox`` mutates its value instead of scrolling the panel. Installed on
    those widgets (see :func:`install_no_scroll_on_inputs`), this eats the wheel
    unless the widget has focus, so scrolling past a field never changes it; a
    focused field (clicked or tabbed into) still adjusts on wheel as usual.
    """

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        if (
            event is not None
            and event.type() == QEvent.Type.Wheel
            and isinstance(obj, (QAbstractSpinBox, QComboBox))
            and not obj.hasFocus()
        ):
            return True  # eat: the unfocused widget's value stays put
        return super().eventFilter(obj, event)


def install_no_scroll_on_inputs(container: QWidget) -> None:
    """Make every spin/combo descendant of ``container`` ignore hover-scroll.

    Sweeps ``QAbstractSpinBox`` and ``QComboBox`` children, sets ``StrongFocus``
    (so the wheel no longer grabs focus) and installs one shared
    :class:`_NoScrollEventFilter` parented to ``container`` — its lifetime ties
    to the container, so it is not garbage-collected. Call after the container's
    widgets are built (e.g. once per settings scroll area). ``QLineEdit`` /
    ``QCheckBox`` are untouched: they do not change value on wheel.
    """
    scroll_filter = _NoScrollEventFilter(container)
    for widget in [
        *container.findChildren(QAbstractSpinBox),
        *container.findChildren(QComboBox),
    ]:
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        widget.installEventFilter(scroll_filter)


# ---------------------------------------------------------------------------
# The shared data surface (decision D42)
# ---------------------------------------------------------------------------


class CellRole(Enum):
    """What a cell holds, which is what decides how it is read.

    Alignment is not decoration: a column of right-aligned numbers lines its
    digits up so magnitudes can be compared down the column, and left-aligned
    text lines its first letters up so names can be scanned. Centring both --
    which every Analytics cell used to do -- gives up both.
    """

    #: Names, titles, dates, sentences. Left, vertically centred.
    TEXT = "text"
    #: Counts, ranks, percentages. Right, vertically centred, tabular figures.
    NUMBER = "number"
    #: A short status or a checkbox. Centred.
    STATE = "state"


_ALIGNMENT: dict[CellRole, Qt.AlignmentFlag] = {
    CellRole.TEXT: Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    CellRole.NUMBER: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
    CellRole.STATE: Qt.AlignmentFlag.AlignCenter,
}


def data_row_height(widget: QWidget) -> int:
    """Return the one row height every data view uses, measured through ``widget``.

    A thin, named application of ``widgets.base.sizing.metric_row_height``: the
    formula stays in one place, and the padding fed to it is the *vertical* cell
    padding the stylesheet will add back, so a row is never sized to its text
    alone and then made to clip it.

    The import is deferred on purpose. ``widgets/base/__init__`` pulls in
    ``enhanced_dialog``, which imports this module, so a module-level import here
    is a real cycle: importing ``qt_helpers`` first fails on
    ``add_min_max_buttons``.
    """
    from anki_miner.gui.widgets.base.sizing import metric_row_height

    return metric_row_height(widget, vertical_padding=CELL_PADDING_Y)


def tabular_figures(font: QFont) -> QFont:
    """Return a copy of ``font`` whose digits all take the same width.

    Proportional figures make "1" narrower than "0", so a count that ticks from
    111 to 200 changes width and the whole column twitches. OpenType's ``tnum``
    feature asks the font for fixed-advance digits instead.

    The tag must be a :class:`QFont.Tag`; the string form raises ``TypeError``.
    Not every physical font implements the feature, so a caller that also needs
    a stable *column* must use :func:`hold_numeric_columns`, which measures what
    was actually rendered.

    Args:
        font: The source font. It is not modified.

    Returns:
        A copy carrying the tabular-figures feature.
    """
    tabular = QFont(font)
    tabular.setFeature(QFont.Tag("tnum"), 1)
    return tabular


class _MetricRowDelegate(QStyledItemDelegate):
    """Floor a plain row at the shared metric height, never shrinking one.

    Lists and trees have no equivalent of a table's vertical-header default
    section size, so the one rule reaches them through the item delegate.
    ``max`` is deliberate: a row set with ``setItemWidget`` (every queue row) is
    measured by its widget's own ``sizeHint`` and must stay unclipped.
    """

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802 (Qt override)
        hint = super().sizeHint(option, index)
        view = self.parent()
        if not isinstance(view, QWidget):  # defensive; always the view in practice
            return hint
        return QSize(hint.width(), max(hint.height(), data_row_height(view)))


def configure_data_view(view: QAbstractItemView) -> None:
    """Apply the shared data-surface baseline to a table, list or tree.

    Applies only what every data view should share: per-pixel scrolling, a row
    height derived from the view's own rendered font, and tabular figures. It
    deliberately does **not** touch selection mode, sorting, drag mode or item
    editing -- those are domain decisions each screen owns, and a helper that
    quietly overrode them would erase queue, chain and theme-tree semantics.

    Tables additionally lose their row-number column and their full grid, and
    are pinned to one elided line per row. A wrapping cell is what let Analytics
    rows reach 59px and show 0.78 rows of 20.

    Call once, after the view is constructed. Repopulating does not invalidate
    it; a live text-scale change does, so re-call it from the screen's
    ``changeEvent`` if the screen outlives one.

    Args:
        view: The table, list or tree to configure.
    """
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setFont(tabular_figures(view.font()))

    if isinstance(view, QTableView):
        view.setShowGrid(False)
        view.setWordWrap(False)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        rows = view.verticalHeader()
        if rows is not None:
            row_height = data_row_height(view)
            rows.setVisible(False)  # the row-number column carries no information
            rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            rows.setMinimumSectionSize(row_height)
            rows.setDefaultSectionSize(row_height)
    else:
        view.setItemDelegate(_MetricRowDelegate(view))


class SortableTableWidgetItem(QTableWidgetItem):
    """A cell that sorts by the value it stands for, not by the text it prints.

    Qt's default comparison is lexicographic, which puts 100 above 20 and sorts
    a formatted date by its first digit. The typed key lives in
    :data:`SORT_ROLE`; comparison only uses it when both sides carry keys of the
    same kind, so a column mixing these items with plain ones still orders
    sensibly instead of raising.

    Null ordering is explicit and up to the caller: the word curator gives an
    unranked word ``inf`` so it stays last ascending.
    """

    def __lt__(self, other: QTableWidgetItem) -> bool:
        own = self.data(SORT_ROLE)
        theirs = other.data(SORT_ROLE) if isinstance(other, QTableWidgetItem) else None
        if isinstance(own, (int, float)) and isinstance(theirs, (int, float)):
            return float(own) < float(theirs)
        if isinstance(own, str) and isinstance(theirs, str):
            return own < theirs
        return bool(super().__lt__(other))


def make_table_item(
    text: str,
    role: CellRole = CellRole.TEXT,
    *,
    sort_value: float | str | None = None,
    copy_text: str | None = None,
    tooltip: str | None = None,
) -> SortableTableWidgetItem:
    """Build a cell that knows its alignment, its sort key and its copy value.

    One constructor so a screen cannot half-configure a cell: every cell gets a
    tooltip (columns elide), a sort key and a copy value, and numbers get their
    alignment from :class:`CellRole` rather than from whoever wrote the loop.

    ``sort_value`` must be a number or a string. Dates are passed as a POSIX
    timestamp: Qt converts a ``datetime`` into a ``QDateTime`` on the way into
    item data, and the value that comes back would no longer be the type the
    comparison expects.

    Args:
        text: What the cell prints. May be elided or truncated.
        role: What the cell holds; decides alignment.
        sort_value: The value the column sorts by. Defaults to ``text``.
        copy_text: The full value a row copy yields. Defaults to ``text``.
        tooltip: Hover text. Defaults to ``text``.

    Returns:
        The configured cell.
    """
    item = SortableTableWidgetItem(text)
    item.setTextAlignment(_ALIGNMENT[role])
    item.setToolTip(text if tooltip is None else tooltip)
    item.setData(SORT_ROLE, text if sort_value is None else sort_value)
    item.setData(COPY_ROLE, text if copy_text is None else copy_text)
    return item


def update_table_item(
    item: QTableWidgetItem,
    text: str,
    *,
    sort_value: float | str | None = None,
    copy_text: str | None = None,
    tooltip: str | None = None,
) -> None:
    """Re-point an existing cell at a new value, on :func:`make_table_item`'s contract.

    A cell whose value changes in place cannot simply be rebuilt: the word
    curator's column 0 carries the row's checkbox state and its original-word
    index, and a re-created row would drop both. So mutation has to update the
    same three things construction sets — display text, sort key, copy value —
    or the cell prints one value while it sorts and copies another. Alignment,
    font and flags do not depend on the value and are left alone.

    Defaults mirror :func:`make_table_item` exactly: an omitted tooltip, sort key
    or copy value falls back to ``text``.

    Args:
        item: The cell to re-point.
        text: What the cell now prints. May be elided or truncated.
        sort_value: The value the column sorts by. Defaults to ``text``.
        copy_text: The full value a row copy yields. Defaults to ``text``.
        tooltip: Hover text. Defaults to ``text``.
    """
    item.setText(text)
    item.setToolTip(text if tooltip is None else tooltip)
    item.setData(SORT_ROLE, text if sort_value is None else sort_value)
    item.setData(COPY_ROLE, text if copy_text is None else copy_text)


def hold_numeric_columns(table: QTableWidget, columns: Sequence[int]) -> None:
    """Fit each numeric column to its widest rendered value, and never shrink it.

    Tabular figures stop digits jittering *within* a column only when the
    physical font implements ``tnum``; the bundled and system fallbacks may not.
    So the column is measured rather than assumed: the widest value actually
    rendered, the header's own hint, and -- crucially -- whatever width the
    column already holds. Growing-only is the whole point. A column that
    re-fitted on every refresh would step left and right as counts changed,
    which is the jitter the feature exists to remove.

    Call after each populate, with the same columns given to
    ``configure_table_header(fit_columns=...)``.

    Args:
        table: The populated table.
        columns: The numeric columns to hold.
    """
    header = table.horizontalHeader()
    if header is None:
        return
    metrics = table.fontMetrics()
    for column in columns:
        widest = 0
        for row in range(table.rowCount()):
            item = table.item(row, column)
            if item is not None:
                widest = max(widest, metrics.horizontalAdvance(item.text()))
        needed = max(widest + 2 * CELL_PADDING, header.sectionSizeHint(column))
        # Only an already-held column has a width worth preserving; before the
        # first call the section is still stretched to fill and means nothing.
        held = (
            header.sectionSize(column) if header.sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive else 0
        )
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(column, max(needed, held))


def _row_payload(view: QAbstractItemView, row: int) -> str:
    """Return the tab-separated copy value of ``row``'s visible cells."""
    model = view.model()
    if model is None:
        return ""
    if isinstance(view, (QTableView, QTreeView)):
        columns = [column for column in range(model.columnCount()) if not view.isColumnHidden(column)]
    else:
        # A list model has exactly one column, and PyQt keeps ``columnCount``
        # private on it, so asking would raise rather than answer 1.
        columns = [0]
    values = []
    for column in columns:
        index = model.index(row, column)
        value = model.data(index, COPY_ROLE)
        if value is None:
            value = model.data(index, Qt.ItemDataRole.DisplayRole)
        text = "" if value is None else str(value)
        if text:  # a checkbox or spacer column contributes nothing to copy
            values.append(text)
    return "\t".join(values)


def install_copy_rows(
    view: QAbstractItemView,
    *,
    row_text: Callable[[int], str] | None = None,
) -> QShortcut:
    """Let the platform copy shortcut lift the selected rows out of ``view``.

    A data surface the user cannot copy out of is a dead end: the word list, the
    session history and the difficulty ranking are all things people paste into
    a spreadsheet or a message. Rows are copied in view order, tab-separated, in
    the cell's full :data:`COPY_ROLE` value rather than the elided display text.
    An empty selection copies nothing rather than silently taking everything.

    A view whose rows are custom widgets has no cell text to serialize and must
    supply ``row_text``.

    Args:
        view: The table, list or tree to make copyable.
        row_text: Optional per-row serializer, given the row index.

    Returns:
        The shortcut, parented to ``view``.
    """
    serialize = row_text if row_text is not None else (lambda row: _row_payload(view, row))

    def copy_selection() -> None:
        selection = view.selectionModel()
        clipboard = QApplication.clipboard()
        if selection is None or clipboard is None:
            return
        rows = sorted({index.row() for index in selection.selectedIndexes()})
        if not rows:
            return
        clipboard.setText("\n".join(serialize(row) for row in rows))

    return scoped_shortcut(view, QKeySequence(QKeySequence.StandardKey.Copy), copy_selection)

"""Tests for gui/utils/qt_helpers: window hints, the wheel filter, and the
shared data-surface contract every table, list and tree is configured through.

Regression guard for the Windows bug where dialogs showed only a close button
(no minimize/maximize). On Windows a plain QDialog gets no min/max buttons unless
the hints are set explicitly; Linux WMs draw all three regardless, so the actual
title-bar buttons can't be pixel-verified on CI. We assert the window flags
instead — that is the mechanism the fix relies on.
"""

from datetime import datetime

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QFont, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.utils.qt_helpers import (
    COPY_ROLE,
    SORT_ROLE,
    CellRole,
    SortableTableWidgetItem,
    _NoScrollEventFilter,
    add_min_max_buttons,
    configure_data_view,
    configure_table_header,
    hold_numeric_columns,
    install_copy_rows,
    install_no_scroll_on_inputs,
    make_table_item,
    tabular_figures,
)
from anki_miner.gui.widgets.base.sizing import metric_row_height

_MIN = Qt.WindowType.WindowMinimizeButtonHint
_MAX = Qt.WindowType.WindowMaximizeButtonHint


@pytest.fixture(autouse=True)
def _no_app_stylesheet(qapp):
    """Measure against widget fonts, not against a leaked application stylesheet.

    A QSS ``font-size`` overrides ``setFont``, so if any earlier file on this
    xdist worker leaves a theme sheet installed, growing a widget's own font
    changes nothing and the row-height assertions below silently stop measuring
    what they name.
    """
    previous = qapp.styleSheet()
    qapp.setStyleSheet("")
    yield
    qapp.setStyleSheet(previous)


def test_add_min_max_buttons_sets_both_hints(qtbot):
    """The helper adds both the minimize and maximize hints."""
    dialog = QDialog()
    qtbot.addWidget(dialog)
    assert not (dialog.windowFlags() & _MIN)  # default QDialog has neither
    assert not (dialog.windowFlags() & _MAX)

    add_min_max_buttons(dialog)

    assert dialog.windowFlags() & _MIN
    assert dialog.windowFlags() & _MAX


def test_add_min_max_buttons_preserves_existing_flags(qtbot):
    """OR-ing the hints keeps pre-existing flags (e.g. the close button)."""
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowType.WindowCloseButtonHint)
    had_close = bool(dialog.windowFlags() & Qt.WindowType.WindowCloseButtonHint)

    add_min_max_buttons(dialog)

    assert bool(dialog.windowFlags() & Qt.WindowType.WindowCloseButtonHint) == had_close
    assert dialog.windowFlags() & _MIN
    assert dialog.windowFlags() & _MAX


def test_word_curation_dialog_has_min_max_buttons(qtbot, make_tokenized_word):
    """The reported dialog (Word Curator) gets both buttons via the helper."""
    from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

    words = [make_tokenized_word(surface="食べた", sentence="食べるのテスト", start_time=0.0, end_time=2.0)]
    dialog = WordCurationDialog(words)
    qtbot.addWidget(dialog)

    assert dialog.windowFlags() & _MIN
    assert dialog.windowFlags() & _MAX


def test_enhanced_dialog_base_has_min_max_buttons(qtbot):
    """EnhancedDialog (base for ResultsDialog/AboutDialog) gets both buttons."""
    from anki_miner.gui.widgets.base.enhanced_dialog import EnhancedDialog

    dialog = EnhancedDialog()
    qtbot.addWidget(dialog)

    assert dialog.windowFlags() & _MIN
    assert dialog.windowFlags() & _MAX


# --- Issue #99: no-scroll wheel filter for settings spin/combo widgets ---
#
# The filter is driven with a real ``QEvent`` (not a Python stub) and a
# monkeypatched ``hasFocus``: the non-eaten branches fall through to
# ``super().eventFilter`` (SIP ``QObject.eventFilter``), which raises TypeError
# on a stub but accepts a real event. ``QEvent(type)`` has a stable single-arg
# constructor, unlike ``QWheelEvent``, and the filter only reads ``event.type()``.


def test_no_scroll_filter_eats_wheel_when_unfocused(qtbot, monkeypatch):
    """Unfocused spin/combo → wheel eaten (returns True), value can't change."""
    spin = QSpinBox()
    qtbot.addWidget(spin)
    filt = _NoScrollEventFilter(spin)
    wheel = QEvent(QEvent.Type.Wheel)

    monkeypatch.setattr(spin, "hasFocus", lambda: False)
    assert filt.eventFilter(spin, wheel) is True


def test_no_scroll_filter_passes_wheel_when_focused(qtbot, monkeypatch):
    """Focused widget → wheel passes through (returns False), value adjusts."""
    combo = QComboBox()
    qtbot.addWidget(combo)
    filt = _NoScrollEventFilter(combo)
    wheel = QEvent(QEvent.Type.Wheel)

    monkeypatch.setattr(combo, "hasFocus", lambda: True)
    assert filt.eventFilter(combo, wheel) is False


def test_no_scroll_filter_ignores_non_wheel_events(qtbot, monkeypatch):
    """Non-wheel events always pass through, even when unfocused."""
    spin = QSpinBox()
    qtbot.addWidget(spin)
    filt = _NoScrollEventFilter(spin)
    press = QEvent(QEvent.Type.MouseButtonPress)

    monkeypatch.setattr(spin, "hasFocus", lambda: False)
    assert filt.eventFilter(spin, press) is False


def test_install_no_scroll_sets_strongfocus_and_parents_filter(qtbot):
    """install_no_scroll_on_inputs sweeps spin/combo children: StrongFocus +
    a single parented filter; QLineEdit is left alone."""
    container = QWidget()
    qtbot.addWidget(container)
    layout = QVBoxLayout(container)
    spin = QSpinBox()
    dspin = QDoubleSpinBox()
    combo = QComboBox()
    line = QLineEdit()
    for w in (spin, dspin, combo, line):
        layout.addWidget(w)

    install_no_scroll_on_inputs(container)

    for w in (spin, dspin, combo):
        assert w.focusPolicy() == Qt.FocusPolicy.StrongFocus
    # exactly one filter, parented to the container (lifetime tied to it).
    assert len(container.findChildren(_NoScrollEventFilter)) == 1


def test_install_no_scroll_eats_wheel_delivered_to_child(qtbot):
    """End-to-end: an (unfocused) wheel delivered to a swept spinbox is eaten,
    so its value does not change."""
    container = QWidget()
    qtbot.addWidget(container)
    layout = QVBoxLayout(container)
    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(5)
    layout.addWidget(spin)

    install_no_scroll_on_inputs(container)

    before = spin.value()
    handled = QApplication.sendEvent(spin, QEvent(QEvent.Type.Wheel))
    assert handled is True  # eaten by the filter
    assert spin.value() == before


# ---------------------------------------------------------------------------
# D42: one data surface for every table, list and tree
# ---------------------------------------------------------------------------


def _grow_font(widget: QWidget) -> None:
    """Grow a widget's own font, the way a raised UI text scale eventually does."""
    font = widget.font()
    font.setPixelSize(font.pixelSize() * 2 if font.pixelSize() > 0 else 28)
    widget.setFont(font)


class TestTabularFigures:
    """Digits must stop jittering as a count updates."""

    def test_sets_the_tnum_feature(self):
        result = tabular_figures(QFont())

        assert QFont.Tag("tnum") in result.featureTags()

    def test_leaves_the_source_font_untouched(self):
        source = QFont()

        tabular_figures(source)

        assert QFont.Tag("tnum") not in source.featureTags()

    def test_keeps_the_rendered_size(self):
        source = QFont()
        source.setPixelSize(23)

        assert tabular_figures(source).pixelSize() == 23


class TestConfigureDataView:
    def test_scrolls_per_pixel_on_both_axes(self, qtbot):
        table = QTableWidget(1, 1)
        qtbot.addWidget(table)

        configure_data_view(table)

        assert table.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
        assert table.horizontalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel

    def test_leaves_selection_sorting_and_drag_alone(self, qtbot):
        """The helper is a visual baseline; domain behaviour stays with the screen."""
        table = QTableWidget(1, 1)
        qtbot.addWidget(table)
        table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        table.setSortingEnabled(True)

        configure_data_view(table)

        assert table.selectionMode() == QAbstractItemView.SelectionMode.MultiSelection
        assert table.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
        assert table.isSortingEnabled()

    def test_hides_the_row_number_column(self, qtbot):
        table = QTableWidget(2, 1)
        qtbot.addWidget(table)

        configure_data_view(table)

        header = table.verticalHeader()
        assert header is not None
        assert header.isHidden()

    def test_drops_the_full_grid(self, qtbot):
        table = QTableWidget(2, 2)
        qtbot.addWidget(table)

        configure_data_view(table)

        assert table.showGrid() is False

    def test_keeps_a_row_to_one_elided_line(self, qtbot):
        table = QTableWidget(1, 1)
        qtbot.addWidget(table)

        configure_data_view(table)

        assert table.wordWrap() is False
        assert table.textElideMode() == Qt.TextElideMode.ElideRight

    def test_table_row_height_is_the_shared_metric(self, qtbot):
        table = QTableWidget(3, 1)
        qtbot.addWidget(table)

        configure_data_view(table)

        header = table.verticalHeader()
        assert header is not None
        assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed
        assert header.defaultSectionSize() == metric_row_height(table)

    def test_table_row_height_tracks_the_font(self, qtbot):
        small = QTableWidget(1, 1)
        qtbot.addWidget(small)
        configure_data_view(small)
        header = small.verticalHeader()
        assert header is not None
        baseline = header.defaultSectionSize()

        large = QTableWidget(1, 1)
        qtbot.addWidget(large)
        _grow_font(large)
        configure_data_view(large)
        grown = large.verticalHeader()
        assert grown is not None

        assert grown.defaultSectionSize() > baseline

    def test_list_rows_use_the_same_metric(self, qtbot):
        view = QListWidget()
        qtbot.addWidget(view)
        view.addItem(QListWidgetItem("食べる"))

        configure_data_view(view)

        assert view.sizeHintForRow(0) >= metric_row_height(view)

    def test_tree_rows_use_the_same_metric(self, qtbot):
        view = QTreeWidget()
        qtbot.addWidget(view)
        view.setColumnCount(1)
        view.addTopLevelItem(QTreeWidgetItem(["食べる"]))

        configure_data_view(view)

        assert view.sizeHintForRow(0) >= metric_row_height(view)

    def test_list_rows_track_the_font(self, qtbot):
        small = QListWidget()
        qtbot.addWidget(small)
        small.addItem(QListWidgetItem("食べる"))
        configure_data_view(small)
        baseline = small.sizeHintForRow(0)

        large = QListWidget()
        qtbot.addWidget(large)
        large.addItem(QListWidgetItem("食べる"))
        _grow_font(large)
        configure_data_view(large)

        assert large.sizeHintForRow(0) > baseline

    def test_embedded_row_widgets_are_not_clipped(self, qtbot):
        """Queue rows are QFrames set via setItemWidget; their sizeHint wins."""
        view = QListWidget()
        qtbot.addWidget(view)
        item = QListWidgetItem()
        view.addItem(item)
        row = QLabel("queued")
        row.setMinimumHeight(90)
        view.setItemWidget(item, row)

        configure_data_view(view)

        assert view.sizeHintForRow(0) >= 90

    def test_numeric_digits_are_tabular(self, qtbot):
        view = QTableWidget(1, 1)
        qtbot.addWidget(view)

        configure_data_view(view)

        assert QFont.Tag("tnum") in view.font().featureTags()


class TestMakeTableItem:
    def test_text_is_left_aligned(self):
        item = make_table_item("Shirobako")

        assert item.textAlignment() == (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def test_numbers_are_right_aligned(self):
        item = make_table_item("142", CellRole.NUMBER)

        assert item.textAlignment() == (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def test_state_is_centred(self):
        item = make_table_item("OK", CellRole.STATE)

        assert item.textAlignment() == Qt.AlignmentFlag.AlignCenter

    def test_tooltip_defaults_to_the_full_text(self):
        item = make_table_item("A very long episode name")

        assert item.toolTip() == "A very long episode name"

    def test_explicit_tooltip_wins(self):
        item = make_table_item("12", CellRole.NUMBER, tooltip="12 cards")

        assert item.toolTip() == "12 cards"

    def test_sort_value_defaults_to_the_text(self):
        item = make_table_item("Shirobako")

        assert item.data(SORT_ROLE) == "Shirobako"

    def test_sort_value_is_the_underlying_number(self):
        item = make_table_item("1,024", CellRole.NUMBER, sort_value=1024)

        assert item.data(SORT_ROLE) == 1024

    def test_copy_value_keeps_the_untruncated_text(self):
        item = make_table_item("A very long sen…", copy_text="A very long sentence, in full.")

        assert item.data(COPY_ROLE) == "A very long sentence, in full."

    def test_roles_do_not_collide_with_user_role(self):
        """Curator column 0 stores its original-index mapping in UserRole."""
        item = make_table_item("x")
        item.setData(Qt.ItemDataRole.UserRole, 7)

        assert item.data(Qt.ItemDataRole.UserRole) == 7
        assert Qt.ItemDataRole.UserRole not in (SORT_ROLE, COPY_ROLE)
        assert SORT_ROLE != COPY_ROLE


class TestSortableTableWidgetItem:
    @staticmethod
    def _sorted_column(qtbot, items: list[SortableTableWidgetItem]) -> list[str]:
        table = QTableWidget(len(items), 1)
        qtbot.addWidget(table)
        for row, item in enumerate(items):
            table.setItem(row, 0, item)
        table.sortItems(0, Qt.SortOrder.AscendingOrder)
        return [table.item(row, 0).text() for row in range(table.rowCount())]

    def test_sorts_by_number_not_by_printed_string(self, qtbot):
        items = [make_table_item(str(n), CellRole.NUMBER, sort_value=n) for n in (100, 20, 3)]

        assert self._sorted_column(qtbot, items) == ["3", "20", "100"]

    def test_sorts_dates_by_their_instant(self, qtbot):
        """Printed day-first, so the display string alone sorts wrongly."""
        stamps = [
            ("01/02/2026", datetime(2026, 2, 1)),
            ("03/01/2026", datetime(2026, 1, 3)),
        ]
        items = [make_table_item(text, sort_value=when.timestamp()) for text, when in stamps]

        assert self._sorted_column(qtbot, items) == ["03/01/2026", "01/02/2026"]

    def test_missing_values_stay_last_ascending(self, qtbot):
        items = [
            make_table_item("50", CellRole.NUMBER, sort_value=50.0),
            make_table_item("-", CellRole.NUMBER, sort_value=float("inf")),
            make_table_item("5", CellRole.NUMBER, sort_value=5.0),
        ]

        assert self._sorted_column(qtbot, items) == ["5", "50", "-"]

    def test_falls_back_to_display_order_for_a_foreign_item(self, qtbot):
        """A plain QTableWidgetItem carries no sort value; comparison still works."""
        table = QTableWidget(2, 1)
        qtbot.addWidget(table)
        table.setItem(0, 0, make_table_item("b", CellRole.NUMBER, sort_value=2))
        table.setItem(1, 0, QTableWidgetItem("a"))
        table.sortItems(0, Qt.SortOrder.AscendingOrder)

        assert [table.item(row, 0).text() for row in range(2)] == ["a", "b"]


class TestConfigureTableHeader:
    def test_default_still_stretches_every_column(self, qtbot):
        table = QTableWidget(1, 3)
        qtbot.addWidget(table)

        configure_table_header(table)

        header = table.horizontalHeader()
        assert header is not None
        assert header.stretchLastSection() is True
        assert all(header.sectionResizeMode(col) == QHeaderView.ResizeMode.Stretch for col in range(3))

    def test_fit_columns_size_to_their_content(self, qtbot):
        table = QTableWidget(1, 3)
        qtbot.addWidget(table)

        configure_table_header(table, fit_columns=(0, 2))

        header = table.horizontalHeader()
        assert header is not None
        assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
        assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch
        assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.ResizeToContents

    def test_a_mixed_header_stops_stretching_its_last_section(self, qtbot):
        """Stretch-last would override a trailing fitted column's width."""
        table = QTableWidget(1, 3)
        qtbot.addWidget(table)

        configure_table_header(table, fit_columns=(2,))

        header = table.horizontalHeader()
        assert header is not None
        assert header.stretchLastSection() is False


class TestHoldNumericColumns:
    @staticmethod
    def _fill(table: QTableWidget, values: list[str]) -> None:
        table.setRowCount(len(values))
        for row, value in enumerate(values):
            table.setItem(row, 0, make_table_item(value, CellRole.NUMBER, sort_value=float(value)))

    def test_fits_the_widest_rendered_value(self, qtbot):
        table = QTableWidget(0, 1)
        qtbot.addWidget(table)
        table.setHorizontalHeaderLabels(["N"])
        configure_data_view(table)
        self._fill(table, ["1", "88888888"])

        hold_numeric_columns(table, (0,))

        assert table.columnWidth(0) >= table.fontMetrics().horizontalAdvance("88888888")

    def test_the_column_never_shrinks(self, qtbot):
        """The physical font may ignore ``tnum``; the column must not jitter."""
        table = QTableWidget(0, 1)
        qtbot.addWidget(table)
        table.setHorizontalHeaderLabels(["N"])
        configure_data_view(table)
        self._fill(table, ["88888888"])
        hold_numeric_columns(table, (0,))
        wide = table.columnWidth(0)

        self._fill(table, ["1"])
        hold_numeric_columns(table, (0,))

        assert table.columnWidth(0) == wide

    def test_the_header_label_still_fits(self, qtbot):
        table = QTableWidget(0, 1)
        qtbot.addWidget(table)
        table.setHorizontalHeaderLabels(["Cards created"])
        configure_data_view(table)
        self._fill(table, ["1"])

        hold_numeric_columns(table, (0,))

        assert table.columnWidth(0) >= table.fontMetrics().horizontalAdvance("Cards created")


class TestInstallCopyRows:
    def test_copies_the_selected_table_row_tab_separated(self, qtbot, qapp):
        table = QTableWidget(1, 3)
        qtbot.addWidget(table)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for col, text in enumerate(("2026-05-16", "Shirobako", "12")):
            table.setItem(0, col, make_table_item(text))
        table.selectRow(0)
        shortcut = install_copy_rows(table)

        shortcut.activated.emit()

        assert qapp.clipboard().text() == "2026-05-16\tShirobako\t12"

    def test_copies_the_untruncated_value_not_the_display_text(self, qtbot, qapp):
        table = QTableWidget(1, 1)
        qtbot.addWidget(table)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setItem(0, 0, make_table_item("これは長い…", copy_text="これは長い例文です。"))
        table.selectRow(0)
        shortcut = install_copy_rows(table)

        shortcut.activated.emit()

        assert qapp.clipboard().text() == "これは長い例文です。"

    def test_skips_empty_cells_such_as_a_checkbox_column(self, qtbot, qapp):
        table = QTableWidget(1, 2)
        qtbot.addWidget(table)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setItem(0, 0, make_table_item("", CellRole.STATE))
        table.setItem(0, 1, make_table_item("食べる"))
        table.selectRow(0)
        shortcut = install_copy_rows(table)

        shortcut.activated.emit()

        assert qapp.clipboard().text() == "食べる"

    def test_copies_selected_list_rows_one_per_line(self, qtbot, qapp):
        view = QListWidget()
        qtbot.addWidget(view)
        view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        view.addItems(["食べる", "走る", "見る"])
        view.item(0).setSelected(True)
        view.item(2).setSelected(True)
        shortcut = install_copy_rows(view)

        shortcut.activated.emit()

        assert qapp.clipboard().text() == "食べる\n見る"

    def test_an_empty_selection_leaves_the_clipboard_alone(self, qtbot, qapp):
        table = QTableWidget(1, 1)
        qtbot.addWidget(table)
        table.setItem(0, 0, make_table_item("x"))
        shortcut = install_copy_rows(table)
        qapp.clipboard().setText("untouched")

        shortcut.activated.emit()

        assert qapp.clipboard().text() == "untouched"

    def test_a_custom_widget_list_supplies_its_own_row_payload(self, qtbot, qapp):
        view = QListWidget()
        qtbot.addWidget(view)
        item = QListWidgetItem()
        view.addItem(item)
        view.setItemWidget(item, QLabel("row 0"))
        item.setSelected(True)
        shortcut = install_copy_rows(view, row_text=lambda row: f"episode {row}")

        shortcut.activated.emit()

        assert qapp.clipboard().text() == "episode 0"

    def test_binds_the_platform_copy_shortcut_to_the_view_only(self, qtbot):
        table = QTableWidget(1, 1)
        qtbot.addWidget(table)

        shortcut = install_copy_rows(table)

        assert shortcut.key() == QKeySequence(QKeySequence.StandardKey.Copy)
        assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut

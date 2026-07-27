"""Tests for the shared calm queue row (D31).

A queue row is one line: title, state word, result count. All live detail moved
to the current-job strip, because the workers run one item at a time and a
per-row telemetry line would be duplicated data on every row but one.

Rows are embedded with ``QListWidget.setItemWidget``, so the row has to paint
its own selection. These tests pin that it paints, and that it paints with the
palette's Highlight role -- the one selection colour every one of the 29 themes
sets (``Theme.apply_to_app`` writes it from ``table-selected-bg``).
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.base.queue_row import QueueRowWidget, state_word

_HIGHLIGHT = QColor("#ff00ff")


@pytest.fixture(autouse=True)
def _pinned_style(qapp):
    """Pin the stylesheet and palette so painted colour is the widget's own.

    A leaked application stylesheet repaints backgrounds and voids every colour
    assertion below.
    """
    previous_sheet = qapp.styleSheet()
    previous_palette = qapp.palette()
    qapp.setStyleSheet("")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Highlight, _HIGHLIGHT)
    palette.setColor(QPalette.ColorRole.Window, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#000000"))
    qapp.setPalette(palette)
    yield qapp
    qapp.setPalette(previous_palette)
    qapp.setStyleSheet(previous_sheet)


def _row(qtbot) -> QueueRowWidget:
    row = QueueRowWidget()
    qtbot.addWidget(row)
    row.resize(300, 30)
    return row


def _painted(row: QueueRowWidget) -> list[QColor]:
    """Render the row and return the colours down its first pixel column."""
    from PyQt6.QtGui import QPixmap

    pixmap = QPixmap(row.size())
    pixmap.fill(QColor("#000000"))
    row.render(pixmap)
    image = pixmap.toImage()
    return [QColor(image.pixel(x, row.height() // 2)) for x in range(row.width())]


# ---------------------------------------------------------------------------
# One line, three facts
# ---------------------------------------------------------------------------


def test_row_renders_title_state_and_result(qtbot) -> None:
    row = _row(qtbot)

    row.render_row(title="Episode 3", state="Complete", result="42 cards")

    assert row.title_label.full_text == "Episode 3"
    assert row.state_label.text() == "Complete"
    assert row.result_label.text() == "42 cards"


def test_row_height_comes_from_font_metrics(qtbot) -> None:
    """Row height tracks the rendered font, not a pixel constant."""
    from anki_miner.gui.widgets.base.sizing import metric_row_height

    row = _row(qtbot)

    assert row.sizeHint().height() == metric_row_height(row, vertical_padding=row.ROW_PADDING_Y)


def test_row_detail_goes_to_the_tooltip_not_a_second_line(qtbot) -> None:
    row = _row(qtbot)

    row.render_row(title="Episode 3", state="Failed", result="", detail="FFmpegError: oops")

    assert "FFmpegError: oops" in row.toolTip()


def test_row_tooltip_clears_when_there_is_no_detail(qtbot) -> None:
    row = _row(qtbot)
    row.render_row(title="Episode 3", state="Failed", result="", detail="boom")

    row.render_row(title="Episode 3", state="Ready", result="")

    assert row.toolTip() == ""


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_row_starts_unselected(qtbot) -> None:
    row = _row(qtbot)

    assert row.is_selected() is False
    assert row.property("queueSelected") is False


def test_set_selected_publishes_a_styling_hook(qtbot) -> None:
    """A dynamic property is the stable handle themes and tests style against."""
    row = _row(qtbot)

    row.set_selected(True)

    assert row.is_selected() is True
    assert row.property("queueSelected") is True


def test_selected_row_paints_the_palette_highlight(qtbot) -> None:
    """Selection is visible without depending on the view painting behind us."""
    row = _row(qtbot)
    row.render_row(title="Episode 3", state="Ready", result="")

    row.set_selected(True)
    QApplication.processEvents()

    columns = _painted(row)
    assert any(c.red() > 0 and c.blue() > 0 for c in columns), "no highlight tint painted"
    # The leading accent bar is the full-strength highlight.
    assert columns[0] == _HIGHLIGHT


def test_unselected_row_paints_no_highlight(qtbot) -> None:
    row = _row(qtbot)
    row.render_row(title="Episode 3", state="Ready", result="")

    columns = _painted(row)

    assert columns[0] != _HIGHLIGHT


def test_deselecting_removes_the_highlight(qtbot) -> None:
    row = _row(qtbot)
    row.set_selected(True)

    row.set_selected(False)
    QApplication.processEvents()

    assert _painted(row)[0] != _HIGHLIGHT


# ---------------------------------------------------------------------------
# Shared state vocabulary
# ---------------------------------------------------------------------------


def test_state_words_are_the_filter_vocabulary() -> None:
    """The row's words and the filter chips must name the same five states."""
    assert state_word("ready") == "Ready"
    assert state_word("running") == "Running"
    assert state_word("failed") == "Failed"
    assert state_word("complete") == "Complete"


def test_unknown_bucket_has_no_word() -> None:
    assert state_word("nonsense") == ""

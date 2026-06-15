"""Tests for :class:`ElidingLabel` — single-line elision + full-text tooltip."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.base.eliding_label import ElidingLabel

_LONG = (
    "This is a very long status line that will not fit inside a narrow label and "
    "therefore must be elided down to a single truncated line ending in an ellipsis."
)


def test_full_text_returns_original(qtbot) -> None:
    label = ElidingLabel()
    qtbot.addWidget(label)
    label.setText("hello world")
    assert label.full_text == "hello world"


def test_short_text_fits_no_tooltip(qtbot) -> None:
    label = ElidingLabel()
    qtbot.addWidget(label)
    label.resize(2000, 20)  # plenty of room
    label.setText("short")
    assert label.text() == "short"
    assert label.toolTip() == ""


def test_long_text_is_elided_with_ellipsis(qtbot) -> None:
    label = ElidingLabel()
    qtbot.addWidget(label)
    label.resize(120, 20)  # narrow — forces elision
    label.setText(_LONG)
    assert label.text().endswith("…")
    assert len(label.text()) < len(_LONG)
    # Original stays intact and reachable.
    assert label.full_text == _LONG


def test_long_text_sets_tooltip_to_full_text(qtbot) -> None:
    label = ElidingLabel()
    qtbot.addWidget(label)
    label.resize(120, 20)
    label.setText(_LONG)
    assert label.toolTip() == _LONG


def test_newlines_collapsed_in_display_but_kept_in_full_text_and_tooltip(qtbot) -> None:
    multiline = "line one\nline two\nline three"
    label = ElidingLabel()
    qtbot.addWidget(label)
    label.resize(2000, 20)  # wide enough that nothing is elided
    label.setText(multiline)
    # Displayed string is one line — newlines collapsed to spaces.
    assert "\n" not in label.text()
    assert label.text() == "line one line two line three"
    # Full text keeps the original newlines for the tooltip.
    assert label.full_text == multiline
    # A collapsed (but not width-elided) string still tooltips the verbatim original.
    assert label.toolTip() == multiline


def test_elide_middle_mode_keeps_both_ends(qtbot) -> None:
    path = "/home/user/very/long/path/to/some/deeply/nested/file_name_here.mkv"
    label = ElidingLabel(mode=Qt.TextElideMode.ElideMiddle)
    qtbot.addWidget(label)
    label.resize(160, 20)
    label.setText(path)
    shown = label.text()
    assert "…" in shown
    assert shown.startswith("/home")
    assert shown.endswith(".mkv")


def test_reelides_on_resize(qtbot) -> None:
    label = ElidingLabel()
    qtbot.addWidget(label)
    label.resize(2000, 20)
    label.setText(_LONG)
    assert label.text() == _LONG  # fits wide
    label.resize(120, 20)  # shrink → width() updates synchronously pre-show
    # Deliver the resize event deterministically: offscreen QPA won't reliably
    # deliver it via show()/processEvents() within one event loop pass.
    QApplication.sendEvent(label, QResizeEvent(QSize(120, 20), QSize(2000, 20)))
    assert label.text().endswith("…")
    assert label.full_text == _LONG

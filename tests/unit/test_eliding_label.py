"""Tests for :class:`ElidingLabel` — single-line elision + full-text tooltip."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.base.eliding_label import ElidingLabel

# QApplication needed for widget instantiation.
_app = QApplication.instance() or QApplication([])

_LONG = (
    "This is a very long status line that will not fit inside a narrow label and "
    "therefore must be elided down to a single truncated line ending in an ellipsis."
)


def test_full_text_returns_original() -> None:
    label = ElidingLabel()
    label.setText("hello world")
    assert label.full_text == "hello world"


def test_short_text_fits_no_tooltip() -> None:
    label = ElidingLabel()
    label.resize(2000, 20)  # plenty of room
    label.setText("short")
    assert label.text() == "short"
    assert label.toolTip() == ""


def test_long_text_is_elided_with_ellipsis() -> None:
    label = ElidingLabel()
    label.resize(120, 20)  # narrow — forces elision
    label.setText(_LONG)
    assert label.text().endswith("…")
    assert len(label.text()) < len(_LONG)
    # Original stays intact and reachable.
    assert label.full_text == _LONG


def test_long_text_sets_tooltip_to_full_text() -> None:
    label = ElidingLabel()
    label.resize(120, 20)
    label.setText(_LONG)
    assert label.toolTip() == _LONG


def test_newlines_collapsed_in_display_but_kept_in_full_text_and_tooltip() -> None:
    multiline = "line one\nline two\nline three"
    label = ElidingLabel()
    label.resize(2000, 20)  # wide enough that nothing is elided
    label.setText(multiline)
    # Displayed string is one line — newlines collapsed to spaces.
    assert "\n" not in label.text()
    assert label.text() == "line one line two line three"
    # Full text keeps the original newlines for the tooltip.
    assert label.full_text == multiline
    # A collapsed (but not width-elided) string still tooltips the verbatim original.
    assert label.toolTip() == multiline


def test_elide_middle_mode_keeps_both_ends() -> None:
    path = "/home/user/very/long/path/to/some/deeply/nested/file_name_here.mkv"
    label = ElidingLabel(mode=Qt.TextElideMode.ElideMiddle)
    label.resize(160, 20)
    label.setText(path)
    shown = label.text()
    assert "…" in shown
    assert shown.startswith("/home")
    assert shown.endswith(".mkv")


def test_reelides_on_resize() -> None:
    label = ElidingLabel()
    label.resize(2000, 20)
    label.show()  # so resizeEvent is delivered when the geometry changes
    _app.processEvents()
    label.setText(_LONG)
    assert label.text() == _LONG  # fits wide
    label.resize(120, 20)  # shrink → resizeEvent re-elides
    _app.processEvents()
    try:
        assert label.text().endswith("…")
        assert label.full_text == _LONG
    finally:
        label.hide()

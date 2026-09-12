"""An install failure reaches a one-line status label; the row must stay one line.

Every in-app installer routes its outcome through ``FormPanel.set_status_text``
as ``str(exc)`` on failure, so the label is handed raw exception text. The
reported case: a macOS dyld failure is three lines, the QLabel rendered all
three, and the settings card cut them at its right edge — the user's screenshot
could not even be read to find which symbol was missing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel

from anki_miner.gui.widgets.base.form_panel import STATUS_LINE_MAX_CHARS, FormPanel

#: The failure from the v3.1.0 macOS arm64 report, shortened but same shape.
_DYLD_ERROR = (
    "dlopen(/Users/x/AnkiMiner/_internal/av/_core.abi3.so, 0x0002): Symbol not found: _foo\n"
    "  Referenced from: <F700D981> /Users/x/AnkiMiner/_internal/av/_core.abi3.so\n"
    "  Expected in:     <3D79D1A7> /System/Library/Frameworks/AVFoundation.framework/AVFoundation"
)


def test_a_multi_line_failure_renders_as_one_line(qtbot) -> None:
    label = QLabel("")
    qtbot.addWidget(label)

    FormPanel.set_status_text(label, _DYLD_ERROR)

    assert "\n" not in label.text()
    assert "  " not in label.text(), "collapsed runs must not leave double spaces"


def test_the_full_failure_stays_readable_on_hover(qtbot) -> None:
    """Verbatim, newlines intact — it is what the user has to quote in a report."""
    label = QLabel("")
    qtbot.addWidget(label)

    FormPanel.set_status_text(label, _DYLD_ERROR)

    assert label.toolTip() == _DYLD_ERROR


def test_an_over_long_line_is_cut_with_an_ellipsis(qtbot) -> None:
    label = QLabel("")
    qtbot.addWidget(label)

    FormPanel.set_status_text(label, "x" * (STATUS_LINE_MAX_CHARS + 50))

    assert len(label.text()) == STATUS_LINE_MAX_CHARS
    assert label.text().endswith("…")


def test_an_ordinary_status_line_is_untouched(qtbot) -> None:
    """The normal path is byte-identical, tooltip included: these labels carry
    short translated strings and the rest of the suite asserts them verbatim."""
    label = QLabel("")
    qtbot.addWidget(label)

    FormPanel.set_status_text(label, "Downloading…")

    assert label.text() == "Downloading…"
    assert label.toolTip() == ""


def test_a_line_exactly_at_the_cap_is_not_cut(qtbot) -> None:
    label = QLabel("")
    qtbot.addWidget(label)
    message = "y" * STATUS_LINE_MAX_CHARS

    FormPanel.set_status_text(label, message)

    assert label.text() == message
    assert label.toolTip() == ""


@pytest.mark.parametrize(
    "module_name",
    [
        "anki_miner.gui.widgets.panels.subtitles_settings_panel",
        "anki_miner.gui.widgets.panels.youtube_settings_panel",
        "anki_miner.gui.widgets.panels.mining_language_settings_panel",
    ],
)
def test_no_status_setter_writes_the_label_directly(module_name: str) -> None:
    """The ``set_*_status`` methods are the only entry points handed ``str(exc)``.
    One that calls ``setText`` on the label directly reopens the bug, so the
    pattern is banned in the three panels that own such a label."""
    import importlib
    from pathlib import Path

    source = importlib.import_module(module_name).__file__
    assert source is not None
    assert "status_label.setText(text)" not in Path(source).read_text(encoding="utf-8")

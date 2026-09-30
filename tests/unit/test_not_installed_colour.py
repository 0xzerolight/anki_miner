"""FINDINGS overlap 19: "Not installed" reads in ONE neutral colour everywhere.

C04 (WS3's Settings install lines) and E08 (Manga OCR's setup card) mark it
``QLabel#validation-status[status="info"]``; E10 (System Health) draws it with
the ``pending`` StatusBadge. Asserted on the live palette, not the rule text.
``solarized-dark`` is in the list because it is one of the two themes whose
``badge-pending-text`` differs from ``text-muted``, so a rule that used the
muted token (or no rule at all) fails there.
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QLabel

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.base import StatusBadge


def _text_colour(widget) -> QColor:
    widget.ensurePolished()
    return widget.palette().color(QPalette.ColorRole.WindowText)


@pytest.mark.parametrize("mode", ["dark", "light", "solarized-dark"])
def test_the_status_line_and_the_health_badge_share_one_grey(mode, qapp, qtbot):
    label = QLabel("Not installed")
    label.setObjectName("validation-status")
    label.setProperty("status", "info")
    badge = StatusBadge("", status="pending", clickable=False)
    qtbot.addWidget(label)
    qtbot.addWidget(badge)
    try:
        qapp.setStyleSheet(Theme.get_stylesheet(mode))

        expected = QColor(Theme.get_colors(mode)["badge-pending-text"])
        assert _text_colour(label) == expected
        assert _text_colour(badge) == expected
    finally:
        qapp.setStyleSheet("")

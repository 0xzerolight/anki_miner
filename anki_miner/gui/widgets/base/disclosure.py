"""A power-user disclosure: a checkbox row that shows extra settings under it."""

from __future__ import annotations

from PyQt6.QtWidgets import QGroupBox, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles import SPACING

#: The title row is as tall as its tick box: ``QGroupBox::indicator`` in
#: common.qss is 18 px plus a 2 px border on each side.
_TITLE_ROW = 22
#: The body starts where the title text does: the tick box, then the same
#: ``spacing-xs`` gap a QCheckBox leaves before its text.
_BODY_INDENT = _TITLE_ROW + SPACING.xs


def make_disclosure(title: str, body: QWidget) -> QGroupBox:
    """Return a collapsed disclosure titled ``title`` that shows ``body`` when ticked.

    A checkable QGroupBox, for its tick, title and keyboard handling. Qt's own
    checkable group only disables its children, so the body is hidden and shown
    instead. common.qss draws ``QGroupBox[disclosure="true"]`` as body text with
    no frame, so it reads as one more checkbox row rather than a heading.
    """
    group = QGroupBox(title)
    group.setCheckable(True)
    group.setChecked(False)
    group.setProperty("disclosure", True)
    layout = QVBoxLayout(group)
    # The title is drawn over the group's top row. An empty layout still counts
    # its margins, so the top margin is exactly that row (a collapsed disclosure
    # stays one row tall) and the gap under the title rides on the body, which is
    # hidden with it.
    layout.setContentsMargins(_BODY_INDENT, _TITLE_ROW, 0, 0)
    body.setContentsMargins(0, SPACING.xxs, 0, 0)
    layout.addWidget(body)
    body.setVisible(False)
    group.toggled.connect(body.setVisible)
    return group

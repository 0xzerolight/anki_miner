"""About at its minimum size keeps every row whole (Z.5: shrunk, it cut the shortcut descenders).

Run under the real theme: the stylesheet's fonts are what wrap the blurb onto
the second line the window's minimum never counted.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QLabel

from anki_miner.gui.utils.key_bindings import about_rows, resolve_bindings
from anki_miner.gui.widgets.dialogs.about_dialog import AboutDialog


def _shown(qtbot) -> AboutDialog:
    dialog = AboutDialog("9.9.9", about_rows(resolve_bindings({})))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    return dialog


@pytest.fixture(params=[1.0, 1.5], ids=["text-1x", "text-1.5x"])
def themed(request, font_scale):
    font_scale(request.param)


def test_the_minimum_height_counts_the_wrapped_blurb(qtbot, themed):
    dialog = _shown(qtbot)

    assert dialog.minimumHeight() >= dialog.heightForWidth(dialog.minimumWidth())


def test_shrinking_to_the_minimum_squeezes_no_shortcut_row(qtbot, themed):
    dialog = _shown(qtbot)
    rows = about_rows(resolve_bindings({}))
    texts = {key for key, _ in rows} | {desc for _, desc in rows}

    dialog.resize(1, 1)
    qtbot.waitUntil(lambda: dialog.height() == dialog.minimumHeight())

    labels = [label for label in dialog.findChildren(QLabel) if label.text() in texts]
    assert labels
    for label in labels:
        assert label.height() >= label.minimumSizeHint().height(), label.text()

"""Tests for gui/utils/qt_helpers, focused on add_min_max_buttons.

Regression guard for the Windows bug where dialogs showed only a close button
(no minimize/maximize). On Windows a plain QDialog gets no min/max buttons unless
the hints are set explicitly; Linux WMs draw all three regardless, so the actual
title-bar buttons can't be pixel-verified on CI. We assert the window flags
instead — that is the mechanism the fix relies on.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog

from anki_miner.gui.utils.qt_helpers import add_min_max_buttons

_MIN = Qt.WindowType.WindowMinimizeButtonHint
_MAX = Qt.WindowType.WindowMaximizeButtonHint


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

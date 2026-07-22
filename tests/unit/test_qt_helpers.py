"""Tests for gui/utils/qt_helpers, focused on add_min_max_buttons.

Regression guard for the Windows bug where dialogs showed only a close button
(no minimize/maximize). On Windows a plain QDialog gets no min/max buttons unless
the hints are set explicitly; Linux WMs draw all three regardless, so the actual
title-bar buttons can't be pixel-verified on CI. We assert the window flags
instead — that is the mechanism the fix relies on.
"""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.utils.qt_helpers import (
    _NoScrollEventFilter,
    add_min_max_buttons,
    install_no_scroll_on_inputs,
)

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

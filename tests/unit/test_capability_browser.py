"""The Usage Guide browser: one list, one Open button, filtering and navigation (E11).

It used to be 95 boxed rows with 82 "Open ▸" buttons and could be shrunk to
94x129. Enter in the search box only searches (D49-B); Enter or a double-click
on a row opens it.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QRect, Qt, QTranslator
from PyQt6.QtGui import QColor, QFontMetrics, QImage, QKeyEvent, QPainter
from PyQt6.QtWidgets import QStyleOptionViewItem

from anki_miner.gui.capabilities import CAPABILITIES, CapabilityTarget
from anki_miner.gui.resources.styles import Theme
from anki_miner.gui.widgets.dialogs.capability_browser import CapabilityBrowser, run_capability_browser
from anki_miner.languages.registry import get_profile


@pytest.fixture
def dialog(qtbot):
    dlg = CapabilityBrowser()
    qtbot.addWidget(dlg)
    yield dlg
    dlg.deleteLater()


def _item_for(dialog, cap_id):
    for row in range(dialog.list.count()):
        item = dialog.list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == cap_id:
            return item
    raise AssertionError(cap_id)


def test_starts_showing_everything(dialog):
    assert dialog._current == list(CAPABILITIES)


def test_every_capability_is_one_list_item(dialog):
    ids = [dialog.list.item(r).data(Qt.ItemDataRole.UserRole) for r in range(dialog.list.count())]

    assert [i for i in ids if i is not None] == [cap.id for cap in CAPABILITIES]


def test_there_is_one_open_button(dialog):
    from PyQt6.QtWidgets import QPushButton

    opens = [b for b in dialog.findChildren(QPushButton) if b.text() == "Open"]

    assert opens == [dialog.open_button]


def test_open_is_not_the_enter_target(dialog):
    """D49-B: Enter in the search box only searches."""
    assert dialog.open_button.autoDefault() is False
    assert dialog.open_button.isDefault() is False
    assert dialog.close_button.autoDefault() is False


def test_the_dialog_cannot_collapse(dialog):
    assert dialog.minimumWidth() >= 480
    assert dialog.minimumHeight() >= 420


def test_typing_filters_to_matches(dialog):
    dialog.search_box.setText("i+1")
    shown = {c.id for c in dialog._current}

    assert "i-plus-one" in shown
    assert "youtube-mining" not in shown


def test_typing_displayed_localized_title_finds_capability(qapp, qtbot):
    class _SpanishTranslator(QTranslator):
        def translate(self, context, source, disambiguation=None, n=-1):  # noqa: N802
            if context == "Capabilities" and source == "Mine a single episode":
                return "Minar un solo episodio"
            return source

    translator = _SpanishTranslator()
    qapp.installTranslator(translator)
    try:
        dlg = CapabilityBrowser()
        qtbot.addWidget(dlg)
        displayed_title = QCoreApplication.translate("Capabilities", "Mine a single episode")

        dlg.search_box.setText(displayed_title)

        assert displayed_title == "Minar un solo episodio"
        assert [cap.id for cap in dlg._current] == ["episode-mining"]
    finally:
        qapp.removeTranslator(translator)


def test_window_title_is_usage_guide(dialog):
    assert dialog.windowTitle() == "Anki Miner Usage Guide"


def test_chrome_texts_translate_under_capabilities_context(qapp, qtbot):
    class _StubTranslator(QTranslator):
        _MAP = {
            "Anki Miner Usage Guide": "TR_TITLE",
            'Search features, e.g. "i+1", "pitch", "youtube"': "TR_PLACEHOLDER",
            "No matching features.": "TR_EMPTY",
            "Open": "TR_OPEN",
        }

        def translate(self, context, source, disambiguation=None, n=-1):  # noqa: N802
            if context == "Capabilities" and source in self._MAP:
                return self._MAP[source]
            return source

    translator = _StubTranslator()
    qapp.installTranslator(translator)
    try:
        dlg = CapabilityBrowser()
        qtbot.addWidget(dlg)

        assert dlg.windowTitle() == "TR_TITLE"
        assert dlg.search_box.placeholderText() == "TR_PLACEHOLDER"
        assert dlg.open_button.text() == "TR_OPEN"
        dlg.search_box.setText("zzzz-nothing-here")
        assert dlg._empty_label.text() == "TR_EMPTY"
    finally:
        qapp.removeTranslator(translator)


def test_open_is_disabled_until_a_row_with_a_place_is_selected(dialog):
    assert dialog.open_button.isEnabled() is False

    dialog.list.setCurrentItem(_item_for(dialog, "system-health"))  # menu-only: nowhere to open
    assert dialog.open_button.isEnabled() is False

    dialog.list.setCurrentItem(_item_for(dialog, "youtube-mining"))
    assert dialog.open_button.isEnabled() is True


def test_category_rows_cannot_be_selected(dialog):
    header = dialog.list.item(0)

    assert header.data(Qt.ItemDataRole.UserRole) is None
    assert not header.flags() & Qt.ItemFlag.ItemIsSelectable


def _paint_option(dialog) -> QStyleOptionViewItem:
    option = QStyleOptionViewItem()
    option.initFrom(dialog.list)
    option.rect = QRect(0, 0, 400, 60)
    option.widget = dialog.list
    return option


def test_a_category_row_is_set_smaller_than_a_feature_title(dialog):
    """B4.3: "Mining workflows" in the feature titles' bold face read as one more feature."""
    delegate = dialog.list.itemDelegate()
    option = _paint_option(dialog)
    model = dialog.list.model()

    category_font, _ = delegate._fonts(option, model.index(0, 0))
    title_font, _ = delegate._fonts(option, model.index(1, 0))

    assert QFontMetrics(category_font).height() < QFontMetrics(title_font).height()


def test_a_category_row_is_painted_in_the_muted_text_colour(dialog):
    delegate = dialog.list.itemDelegate()
    option = _paint_option(dialog)
    image = QImage(option.rect.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    try:
        delegate.paint(painter, option, dialog.list.model().index(0, 0))
    finally:
        painter.end()

    painted = {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height())}
    assert QColor(Theme.get_colors()["text-muted"]).name() in painted


def test_the_open_button_opens_the_selected_row(dialog, qtbot):
    dialog.list.setCurrentItem(_item_for(dialog, "audiobook-mining"))

    with qtbot.waitSignal(dialog.accepted, timeout=1000):
        dialog.open_button.click()

    assert dialog.selected_target == CapabilityTarget("audiobook")


def test_double_click_opens_a_row(dialog, qtbot):
    item = _item_for(dialog, "youtube-mining")

    with qtbot.waitSignal(dialog.accepted, timeout=1000):
        dialog.list.itemDoubleClicked.emit(item)

    assert dialog.selected_target == CapabilityTarget("video", "youtube")


def test_enter_in_the_list_opens_the_current_row(dialog, qtbot):
    dialog.list.setCurrentItem(_item_for(dialog, "youtube-mining"))
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)

    with qtbot.waitSignal(dialog.accepted, timeout=1000):
        QCoreApplication.sendEvent(dialog.list, event)


def test_no_match_shows_empty_state(dialog):
    dialog.search_box.setText("zzzz-nothing-here")

    assert dialog._current == []
    assert dialog._empty_label.isVisibleTo(dialog)


def test_clearing_search_restores_full_list(dialog):
    dialog.search_box.setText("pitch")
    assert len(dialog._current) < len(CAPABILITIES)

    dialog.search_box.setText("")

    assert dialog._current == list(CAPABILITIES)


def test_choosing_records_target_and_accepts(dialog, qtbot):
    target = CapabilityTarget("settings", "filtering")
    cap = next(c for c in CAPABILITIES if c.target == target)

    with qtbot.waitSignal(dialog.accepted, timeout=1000):
        dialog._choose(cap)

    assert dialog.selected_target == target


def test_capability_set_gates_the_listed_rows(qtbot):
    dlg = CapabilityBrowser(capabilities=get_profile("zh").capabilities)
    qtbot.addWidget(dlg)
    shown = {cap.id for cap in dlg._current}

    assert "pinyin" in shown
    assert "furigana" not in shown


def test_capability_set_survives_a_search(qtbot):
    dlg = CapabilityBrowser(capabilities=get_profile("zh").capabilities)
    qtbot.addWidget(dlg)

    dlg.search_box.setText("reading")

    assert dlg._current
    assert all(cap.id != "furigana" for cap in dlg._current)


def test_no_description_keeps_the_ascii_arrow():
    """E11: the user-facing arrows are real arrows now."""
    offenders = [cap.id for cap in CAPABILITIES if " -> " in cap.description]

    assert offenders == []


def test_runner_passes_the_capability_set_to_the_dialog(qtbot, monkeypatch):
    main_window = Mock()
    seen: list[frozenset[str] | None] = []

    def fake_exec(self):
        seen.append(self._capabilities)
        self.selected_target = None
        return 0

    monkeypatch.setattr(CapabilityBrowser, "exec", fake_exec)
    run_capability_browser(None, main_window, get_profile("zh").capabilities)

    assert seen == [get_profile("zh").capabilities]


def test_runner_navigates_on_selection(qtbot, monkeypatch):
    main_window = Mock()
    target = CapabilityTarget("video")

    def fake_exec(self):
        self.selected_target = target
        return 1

    monkeypatch.setattr(CapabilityBrowser, "exec", fake_exec)
    run_capability_browser(None, main_window)
    main_window.reveal_capability.assert_called_once_with(target)


def test_runner_noops_when_dismissed(qtbot, monkeypatch):
    main_window = Mock()

    def fake_exec(self):
        self.selected_target = None
        return 0

    monkeypatch.setattr(CapabilityBrowser, "exec", fake_exec)
    run_capability_browser(None, main_window)
    main_window.reveal_capability.assert_not_called()

"""Mined-content typography routes through one profile-driven helper."""

from __future__ import annotations

import dataclasses
import inspect

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import fonts
from anki_miner.gui.utils.content_text import (
    apply_content_font,
    content_cell_font,
    content_phrase_wrap,
)
from anki_miner.gui.utils.phrase_wrap import phrase_wrap_ja
from anki_miner.languages.profile import ContentTextStyle
from anki_miner.languages.registry import get_profile

JA = get_profile("ja").content_style
FAKE = ContentTextStyle(font_role="zh", families=("Noto Sans SC",), wrap=lambda s: s + "!")


def test_ja_cell_font_is_the_existing_face(qapp):
    assert content_cell_font(JA).families() == fonts.japanese_cell_font().families()


def test_non_ja_cell_font_uses_the_style_families(qapp):
    assert "Noto Sans SC" in content_cell_font(FAKE).families()


def test_wrap_is_the_style_callable():
    assert content_phrase_wrap("これは日本語です。", JA) == phrase_wrap_ja("これは日本語です。")
    assert content_phrase_wrap("abc", FAKE) == "abc!"


def test_apply_marks_the_content_property(qtbot):
    from PyQt6.QtWidgets import QLabel

    label = QLabel()
    qtbot.addWidget(label)
    apply_content_font(label, FAKE, role=fonts.JAPANESE_FEATURE)
    assert label.property(fonts.JAPANESE_PROPERTY) == fonts.JAPANESE_FEATURE
    assert "Noto Sans SC" in label.font().families()


def test_all_eight_consumers_route_through_the_helper():
    from anki_miner.gui.widgets import (
        analytics_tab,
        backfill_tab,
        deck_filter_tab,
        reading_text_tab,
        subtitle_player_widget,
        subtitle_viewer,
    )
    from anki_miner.gui.widgets.dialogs import known_words_dialog, word_curation_dialog

    for cls in (
        analytics_tab.AnalyticsTab,
        known_words_dialog.KnownWordsManagerDialog,
        word_curation_dialog.WordCurationDialog,
        subtitle_player_widget.SubtitlePlayerWidget,
        subtitle_viewer.SubtitleViewer,
    ):
        assert "content_style" in inspect.signature(cls.__init__).parameters, cls

    for module in (
        analytics_tab,
        backfill_tab,
        deck_filter_tab,
        reading_text_tab,
        subtitle_player_widget,
        subtitle_viewer,
        known_words_dialog,
        word_curation_dialog,
    ):
        src = inspect.getsource(module)
        assert "japanese_cell_font(" not in src, module
        assert "apply_japanese_font(" not in src, module
        assert "phrase_wrap_ja(" not in src, module


def test_backfill_tab_derives_its_style_from_config(qtbot):
    from anki_miner.gui.widgets.backfill_tab import CardBackfillTab

    tab = CardBackfillTab(dataclasses.replace(AnkiMinerConfig(), language="ja"))
    qtbot.addWidget(tab)
    assert tab._content_style == get_profile("ja").content_style


def test_player_widget_holds_the_style_it_was_given(qtbot):
    from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget

    widget = SubtitlePlayerWidget(content_style=FAKE)
    qtbot.addWidget(widget)
    assert widget._content_style is FAKE


RTL = ContentTextStyle(font_role="fa", families=("Noto Sans SC",), wrap=lambda s: s, direction="rtl")
PROBED = ContentTextStyle(
    font_role="th",
    families=("Zzz Thai",),
    wrap=lambda s: s,
    writing_system="Thai",
    bundled_fallback=fonts.BUNDLED_JAPANESE_FILE,
)


def test_an_rtl_style_flips_the_content_widget_and_its_children(qtbot):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QListWidget

    widget = QListWidget()
    qtbot.addWidget(widget)
    apply_content_font(widget, RTL)
    assert widget.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert widget.viewport().layoutDirection() == Qt.LayoutDirection.RightToLeft


@pytest.mark.parametrize("back", [FAKE, JA], ids=["ltr-profile", "japanese"])
def test_leaving_rtl_restores_the_inherited_direction(qtbot, back):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QListWidget, QWidget

    parent = QWidget()
    qtbot.addWidget(parent)
    widget = QListWidget(parent)
    apply_content_font(widget, RTL)
    apply_content_font(widget, back)
    assert widget.layoutDirection() == Qt.LayoutDirection.LeftToRight
    assert not widget.testAttribute(Qt.WidgetAttribute.WA_SetLayoutDirection)


@pytest.mark.parametrize("style", [FAKE, JA], ids=["ltr-profile", "japanese"])
def test_an_ltr_style_never_touches_the_direction(qtbot, style):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel

    label = QLabel()
    qtbot.addWidget(label)
    apply_content_font(label, style)
    assert not label.testAttribute(Qt.WidgetAttribute.WA_SetLayoutDirection)


def test_rtl_content_never_flips_the_application(qtbot, qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel

    label = QLabel()
    qtbot.addWidget(label)
    apply_content_font(label, RTL)
    assert qapp.layoutDirection() == Qt.LayoutDirection.LeftToRight


def test_a_style_with_no_script_keeps_its_exact_stylesheet(qtbot):
    from PyQt6.QtWidgets import QLabel

    label = QLabel()
    qtbot.addWidget(label)
    apply_content_font(label, FAKE)
    assert label.styleSheet() == "*[japanese=\"body\"] { font-family: 'Noto Sans SC'; }"


class TestResolvedFamilies:
    """A style that names a script draws with the S22 resolution, in the font AND the sheet."""

    @pytest.fixture(autouse=True)
    def _bare_machine(self, qapp, monkeypatch):
        fonts.reset_font_cache()
        monkeypatch.setattr(fonts, "_script_families", lambda writing_system: [])
        yield
        fonts.reset_font_cache()

    def test_the_cell_font_leads_with_the_bundled_face(self):
        from PyQt6.QtGui import QFontDatabase

        families = content_cell_font(PROBED).families()
        assert families[1:] == ["Zzz Thai"]
        assert families[0] in set(QFontDatabase.families())

    def test_apply_leads_with_the_bundled_face_in_font_and_stylesheet(self, qtbot):
        from PyQt6.QtWidgets import QLabel

        label = QLabel()
        qtbot.addWidget(label)
        apply_content_font(label, PROBED)
        registered = label.font().families()[0]
        assert registered != "Zzz Thai"
        assert f"font-family: '{registered}', 'Zzz Thai';" in label.styleSheet()

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

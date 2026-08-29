"""Mined-content typography follows an IN-SESSION language switch.

Both surfaces captured their face when they were built, so the switch landed
everywhere except here: Reading -> Text kept the outgoing language's face and
its Japanese leading, and Analytics kept it in the series and episode cells,
until the app was restarted. ``MainWindow.sync_mining_language_surfaces`` is the
one place both switch triggers pass through, so it is where the new face is
pushed out from.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.resources.styles import TYPOGRAPHY
from anki_miner.gui.utils import fonts
from anki_miner.gui.utils.content_text import apply_content_font
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
from anki_miner.languages.registry import get_profile
from anki_miner.models.stats import DifficultyEntry, MiningSession

JA = get_profile("ja").content_style
ZH = get_profile("zh").content_style

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"

#: Qt's own default: no proportional line height has been merged in.
_QT_DEFAULT_LINE_HEIGHT = 0.0


@pytest.fixture
def text_tab(qtbot, test_config):
    """A live Reading -> Text tab built for Japanese."""
    with patch(_WORKER_TARGET, autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        tab = ReadingTextTab(
            config=dataclasses.replace(test_config, language="ja"),
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
    qtbot.addWidget(tab)
    yield tab
    tab.deleteLater()


class TestTheWidgetFamilyPinComesOffAgain:
    """The non-ja branch pins its families in a WIDGET stylesheet.

    That sheet outranks both the application sheet and ``setFont``, so leaving
    it installed would make a switch back to Japanese a no-op on screen.
    """

    def test_a_zh_pin_is_removed_when_ja_comes_back(self, qtbot):
        from PyQt6.QtWidgets import QLabel

        label = QLabel()
        qtbot.addWidget(label)

        apply_content_font(label, ZH, role=fonts.JAPANESE_BODY)
        assert ZH.families[0] in label.font().families()

        apply_content_font(label, JA, role=fonts.JAPANESE_BODY)
        assert label.styleSheet() == ""
        assert ZH.families[0] not in label.font().families()

    def test_a_consumers_own_stylesheet_survives(self, qtbot):
        """Only the marker this helper writes is cleared, never anything else."""
        from PyQt6.QtWidgets import QLabel

        label = QLabel()
        qtbot.addWidget(label)
        label.setStyleSheet("QLabel { color: red; }")

        apply_content_font(label, JA, role=fonts.JAPANESE_BODY)

        assert label.styleSheet() == "QLabel { color: red; }"


class TestTheReadingTextBuffer:
    def test_a_switch_to_zh_takes_the_face_and_drops_the_leading(self, text_tab):
        text_tab.text_edit.setPlainText("本文です。\n二行目です。")
        assert text_tab.text_edit.document().firstBlock().blockFormat().lineHeight() == (
            TYPOGRAPHY.japanese_leading_percent
        )

        text_tab.set_content_style(ZH)

        assert ZH.families[0] in text_tab.text_edit.font().families()
        assert text_tab.text_edit.document().firstBlock().blockFormat().lineHeight() == _QT_DEFAULT_LINE_HEIGHT

    def test_the_leading_keeper_is_unwired_on_the_way_out(self, text_tab):
        assert text_tab.text_edit.receivers(text_tab.text_edit.textChanged) == 2

        text_tab.set_content_style(ZH)
        assert text_tab.text_edit.receivers(text_tab.text_edit.textChanged) == 1

        # Replacing the text is what used to re-merge the Japanese leading.
        text_tab.text_edit.setPlainText("学习中文需要每天练习。\n第二行。")
        assert text_tab.text_edit.document().firstBlock().blockFormat().lineHeight() == _QT_DEFAULT_LINE_HEIGHT

    def test_a_switch_back_to_ja_restores_the_face_and_the_keeper(self, text_tab):
        text_tab.set_content_style(ZH)
        text_tab.set_content_style(JA)

        assert ZH.families[0] not in text_tab.text_edit.font().families()
        assert text_tab.text_edit.receivers(text_tab.text_edit.textChanged) == 2

        text_tab.text_edit.setPlainText("本文です。\n二行目です。")
        assert text_tab.text_edit.document().firstBlock().blockFormat().lineHeight() == (
            TYPOGRAPHY.japanese_leading_percent
        )

    def test_repeating_a_style_never_double_wires_the_keeper(self, text_tab):
        """A Qt connection is not idempotent; sync runs on every switch."""
        text_tab.set_content_style(JA)
        text_tab.set_content_style(JA)

        assert text_tab.text_edit.receivers(text_tab.text_edit.textChanged) == 2


def _session(name: str) -> MiningSession:
    from datetime import datetime

    return MiningSession(
        series_name=name,
        episode_name=name,
        mined_at=datetime(2026, 1, 1, 12, 0),
        total_words=10,
        unknown_words=5,
        cards_created=5,
    )


class TestTheAnalyticsNameCells:
    def test_a_switch_re_faces_the_rows_already_on_screen(self, qtbot):
        from anki_miner.gui.widgets.analytics_tab import AnalyticsTab

        tab = AnalyticsTab(MagicMock(name="StatsService"), content_style=JA)
        qtbot.addWidget(tab)
        try:
            tab._update_recent_sessions([_session("シリーズ")])
            tab._update_difficulty_ranking(
                [DifficultyEntry(series_name="シリーズ", total_words=10, unknown_words=5, difficulty_score=0.5)]
            )

            tab.set_content_style(ZH)

            assert tab._content_style is ZH
            for column in (1, 2):
                assert ZH.families[0] in tab.sessions_table.item(0, column).font().families()
            assert ZH.families[0] in tab.difficulty_table.item(0, 1).font().families()
        finally:
            tab.deleteLater()

    def test_the_count_cells_are_left_alone(self, qtbot):
        from anki_miner.gui.widgets.analytics_tab import AnalyticsTab

        tab = AnalyticsTab(MagicMock(name="StatsService"), content_style=JA)
        qtbot.addWidget(tab)
        try:
            tab._update_recent_sessions([_session("シリーズ")])
            tab.set_content_style(ZH)
            assert ZH.families[0] not in tab.sessions_table.item(0, 3).font().families()
        finally:
            tab.deleteLater()


class TestTheSwitchReachesBothTabs:
    """Both triggers converge on ``sync_mining_language_surfaces``."""

    def test_sync_pushes_the_live_language_face_to_every_tab(self, wired_window):
        window, _titles, tabs = wired_window
        text_edit = tabs["Reading"].text_tab.text_edit
        analytics = tabs["Analytics"]
        assert ZH.families[0] not in text_edit.font().families()

        window.config = dataclasses.replace(window.config, language="zh")
        window.sync_mining_language_surfaces()

        assert ZH.families[0] in text_edit.font().families()
        assert analytics._content_style is ZH

    def test_sync_puts_japanese_back(self, wired_window):
        window, _titles, tabs = wired_window
        text_edit = tabs["Reading"].text_tab.text_edit

        window.config = dataclasses.replace(window.config, language="zh")
        window.sync_mining_language_surfaces()
        window.config = dataclasses.replace(window.config, language="ja")
        window.sync_mining_language_surfaces()

        assert ZH.families[0] not in text_edit.font().families()
        assert tabs["Analytics"]._content_style is JA

"""Reading -> Text applies JAPANESE typography only when mining Japanese.

The paste box got ``apply_japanese_block_format`` plus a ``textChanged`` keeper
unconditionally, so Chinese text was laid out on the Japanese leading. Leading
is a ``QTextBlockFormat``, not a stylesheet rule (Qt has no ``line-height``), so
it cannot be language-scoped in the QSS -- it has to be gated in Python.

The ja path must stay byte-identical: same two calls, same order, same wiring.
The pre-existing ``TestJapaneseTypography`` in ``test_reading_text_tab.py``
covers ja and is not edited; this file adds the non-ja half.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles import TYPOGRAPHY
from anki_miner.gui.widgets import reading_text_tab as tab_module
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"

#: Qt's own default: no proportional line height has been merged in.
_QT_DEFAULT_LINE_HEIGHT = 0.0


@pytest.fixture
def make_tab(qtbot, test_config: AnkiMinerConfig):
    """Build a ReadingTextTab for a language, recording block-format calls."""
    built: list[ReadingTextTab] = []

    def _make(language: str, monkeypatch) -> tuple[ReadingTextTab, list[object]]:
        calls: list[object] = []
        monkeypatch.setattr(
            tab_module,
            "apply_japanese_block_format",
            lambda document: calls.append(document),
        )
        config = dataclasses.replace(test_config, language=language)
        with patch(_WORKER_TARGET, autospec=False) as queue_cls:
            queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            widget = ReadingTextTab(
                config=config,
                processor=MagicMock(name="EpisodeProcessor"),
                presenter=MagicMock(name="Presenter"),
            )
        qtbot.addWidget(widget)
        # addWidget holds only a weakref; keep the tab alive for the test.
        built.append(widget)
        return widget, calls

    yield _make
    for widget in built:
        widget.deleteLater()


class TestChineseGetsNoJapaneseTypography:
    def test_the_block_format_is_never_applied(self, make_tab, monkeypatch):
        _tab, calls = make_tab("zh", monkeypatch)
        assert calls == []

    def test_replacing_the_text_leaves_qt_default_leading(self, make_tab, monkeypatch):
        tab, calls = make_tab("zh", monkeypatch)
        tab.text_edit.setPlainText("学习中文需要每天练习。\n第二行。")
        # setPlainText replaces every block; the ja keeper would re-merge here.
        assert calls == []
        block = tab.text_edit.document().firstBlock()
        assert block.blockFormat().lineHeight() == _QT_DEFAULT_LINE_HEIGHT

    def test_the_leading_keeper_is_not_wired(self, make_tab, monkeypatch):
        tab, _calls = make_tab("zh", monkeypatch)
        assert tab.text_edit.receivers(tab.text_edit.textChanged) == 1


class TestJapaneseKeepsIt:
    def test_the_block_format_is_applied_at_construction(self, make_tab, monkeypatch):
        tab, calls = make_tab("ja", monkeypatch)
        assert calls == [tab.text_edit.document()]

    def test_the_leading_keeper_is_wired(self, make_tab, monkeypatch):
        tab, _calls = make_tab("ja", monkeypatch)
        assert tab.text_edit.receivers(tab.text_edit.textChanged) == 2

    def test_replacing_the_text_reapplies_the_leading(self, qtbot, test_config):
        """End to end, with the real helper: the pre-existing ja guarantee."""
        config = dataclasses.replace(test_config, language="ja")
        with patch(_WORKER_TARGET, autospec=False) as queue_cls:
            queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            tab = ReadingTextTab(
                config=config,
                processor=MagicMock(name="EpisodeProcessor"),
                presenter=MagicMock(name="Presenter"),
            )
        qtbot.addWidget(tab)
        try:
            tab.text_edit.setPlainText("本文です。\n二行目です。")
            block = tab.text_edit.document().firstBlock()
            assert block.blockFormat().lineHeight() == TYPOGRAPHY.japanese_leading_percent
        finally:
            tab.deleteLater()

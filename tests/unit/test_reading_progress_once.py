"""D1 for the five Reading sub-tabs: no Progress block; the bar carries the run."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel

from anki_miner.gui.controllers.task_registry import TaskRegistry
from anki_miner.gui.widgets.reading_deck_tab import ReadingDeckTab
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
from anki_miner.models import ProcessingResult
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.models.reading_queue import ReadingQueueItem

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_CLASSES = [ReadingMangaTab, ReadingNovelsTab, ReadingSubtitlesTab, ReadingTextTab, ReadingDeckTab]


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _build(qtbot, cls, config):
    with patch(_WORKER_TARGET):
        widget = cls(config=config, processor=MagicMock(), presenter=MagicMock())
    qtbot.addWidget(widget)
    return widget


def _state_widget(tab):
    return tab.progress_widget if isinstance(tab, ReadingNovelsTab) else tab.overall_progress_widget


@pytest.mark.parametrize("cls", _CLASSES, ids=lambda cls: cls.__name__)
def test_no_progress_block(qtbot, test_config, cls):
    tab = _build(qtbot, cls, test_config)
    assert _state_widget(tab).isHidden()
    assert "Progress" not in [label.text() for label in tab.findChildren(QLabel)]


@pytest.mark.parametrize("cls", _CLASSES, ids=lambda cls: cls.__name__)
def test_the_run_is_told_to_the_pinned_bar(qtbot, test_config, registry, cls):
    tab = _build(qtbot, cls, test_config)
    tab.bind_task_registry(registry)
    item = ReadingQueueItem(source=ReadingSourceRef(kind="text", title="Text", text="x"), title="Text", kind="text")
    tab._run_items = [item]
    # _launch_run seeds the per-run tallies _on_item_finished adds to.
    tab._reset_run_state(1)
    tab._publish_task_start("Reading", total=1)

    tab._on_item_started(0)
    tab._on_item_progress(0, "Extracting media (3 of 5)")
    snapshot = registry.snapshot(tab.TASK_ID)
    assert snapshot is not None
    assert snapshot.detail.endswith("Extracting media (3 of 5)")

    item.status = ReadyItemStatus.COMPLETED
    tab._on_item_finished(0, ProcessingResult(total_words_found=1, new_words_found=1, cards_created=1), None, 1)
    snapshot = registry.snapshot(tab.TASK_ID)
    assert (snapshot.current, snapshot.total) == (1, 1)

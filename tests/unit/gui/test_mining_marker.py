"""The window's mining marker: held while a mining screen's run is in the task registry."""

from __future__ import annotations

import os

import pytest

from anki_miner.cli.entry import _held
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.mining_marker import MiningMarker, mining_task_ids
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry, TaskSpec


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _start(registry: TaskRegistry, task_id: str):
    return registry.start(TaskSpec(task_id=task_id, title=task_id, owner=CapabilityTarget("video", "single")))


def _path(tmp_path):
    return tmp_path / f"instance.mining-{os.getpid()}.lock"


def test_held_while_a_mining_run_is_in_the_registry(registry, tmp_path) -> None:
    marker = MiningMarker(registry, tmp_path, {"run.single"})
    handle = _start(registry, "run.single")
    assert marker.held and _held(_path(tmp_path))
    handle.finish(TaskOutcome.SUCCEEDED)
    assert not marker.held and not _path(tmp_path).exists()


def test_a_tool_run_takes_no_marker(registry, tmp_path) -> None:
    marker = MiningMarker(registry, tmp_path, {"run.single"})
    _start(registry, "tools.backfill")
    assert not marker.held and not _path(tmp_path).exists()


def test_released_only_when_the_last_mining_run_ends(registry, tmp_path) -> None:
    marker = MiningMarker(registry, tmp_path, {"run.single", "queue.youtube"})
    single = _start(registry, "run.single")
    youtube = _start(registry, "queue.youtube")
    single.finish(TaskOutcome.CANCELLED)
    assert marker.held
    youtube.finish(TaskOutcome.FAILED)
    assert not marker.held and not _path(tmp_path).exists()


def test_release_is_idempotent(registry, tmp_path) -> None:
    marker = MiningMarker(registry, tmp_path, {"run.single"})
    _start(registry, "run.single")
    marker.release()
    marker.release()
    assert not marker.held and not _path(tmp_path).exists()


def test_the_mining_screens_are_the_mining_tab_bases() -> None:
    from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
    from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
    from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
    from anki_miner.gui.widgets.condense_tab import CondenseTab
    from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
    from anki_miner.gui.widgets.reading_deck_tab import ReadingDeckTab
    from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
    from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
    from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
    from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
    from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
    from anki_miner.gui.widgets.youtube_tab import YouTubeTab

    screens = [
        SingleEpisodeTab,
        BatchProcessingTab,
        DeckBuilderTab,
        YouTubeTab,
        AudiobookTab,
        ReadingTextTab,
        ReadingSubtitlesTab,
        ReadingMangaTab,
        ReadingNovelsTab,
        ReadingDeckTab,
        CardBackfillTab,
        CondenseTab,
    ]
    assert mining_task_ids(screens) == {
        "run.single",
        "run.batch",
        "run.deckbuilder",
        "queue.youtube",
        "queue.audiobook",
        "queue.reading.text",
        "queue.reading.subtitles",
        "queue.reading.manga",
        "queue.reading.novels",
        "queue.reading.deck",
    }

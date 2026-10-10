"""Every screen that runs work publishes it to the task registry (D17, D22).

Until this landed only the YouTube and Audio queues wrote to ``TaskRegistry``,
so the ticking wait clock and the "Finishing <phase>" explanation behind Cancel
existed on exactly two screens. Single, Batch, the four Reading screens, the
three file tools and Backfill got a frozen bar and a disabled *Cancelling…* and
nothing else, and their pinned action bars stayed collapsed for the same reason.

These tests pin the contract rather than the pixels: the screen declares a
stable id and owner, a run creates a snapshot, Cancel marks it cancelling, and
the run's thread end closes it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from anki_miner.gui.capabilities import MAIN_TABS, SUBTAB_KEYS
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry
from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.booksync_tab import BookSyncTab
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.gui.widgets.deck_filter_tab import DeckFilterTab
from anki_miner.gui.widgets.download_tab import DownloadTab
from anki_miner.gui.widgets.mokuro_tab import MokuroTab
from anki_miner.gui.widgets.readability_tab import ReadabilityTab
from anki_miner.gui.widgets.reading_deck_tab import ReadingDeckTab
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab
from anki_miner.gui.widgets.tracks_tab import TracksTab
from anki_miner.gui.widgets.video_ocr_tab import VideoOcrTab
from anki_miner.gui.workers.file_queue_worker import UNMEASURED

#: Every screen W1-T6 left unpublished, with the id and owner it now declares.
PUBLISHING_SCREENS = [
    (SingleEpisodeTab, "run.single", ("video", "single")),
    (BatchProcessingTab, "run.batch", ("video", "batch")),
    (DeckBuilderTab, "run.deckbuilder", ("video", "deckbuilder")),
    (ReadingMangaTab, "queue.reading.manga", ("reading", "manga")),
    (ReadingNovelsTab, "queue.reading.novels", ("reading", "novels")),
    (ReadingSubtitlesTab, "queue.reading.subtitles", ("reading", "subtitles")),
    (ReadingTextTab, "queue.reading.text", ("reading", "text")),
    (ReadingDeckTab, "queue.reading.deck", ("reading", "deck")),
    (SubtitleCreationTab, "tools.generate", ("subtitles", "generate")),
    (SubtitleRetimeTab, "tools.retime", ("subtitles", "retime")),
    (CondenseTab, "tools.condense", ("subtitles", "condense")),
    (CardBackfillTab, "tools.backfill", ("subtitles", "backfill")),
    (DeckFilterTab, "tools.deckfilter", ("subtitles", "deckfilter")),
    (DownloadTab, "tools.download", ("subtitles", "download")),
    (MokuroTab, "tools.mokuro", ("subtitles", "mokuro")),
    (BookSyncTab, "tools.booksync", ("subtitles", "booksync")),
    (ReadabilityTab, "tools.readability", ("subtitles", "readability")),
    (TracksTab, "tools.tracks", ("subtitles", "tracks")),
    (VideoOcrTab, "tools.videoocr", ("subtitles", "videoocr")),
]


@pytest.mark.parametrize(
    ("screen_cls", "task_id", "owner"),
    PUBLISHING_SCREENS,
    ids=[c.__name__ for c, _, _ in PUBLISHING_SCREENS],
)
def test_screen_declares_a_stable_task_identity(screen_cls, task_id, owner):
    """Ids are unique and owners resolve to a real tab, never a tab index."""
    assert task_id == screen_cls.TASK_ID
    assert screen_cls.TASK_OWNER is not None
    assert screen_cls.TASK_OWNER.main_tab in MAIN_TABS
    subtab = screen_cls.TASK_OWNER.subtab
    assert (screen_cls.TASK_OWNER.main_tab, subtab) == owner
    if subtab is not None:
        assert subtab in SUBTAB_KEYS[screen_cls.TASK_OWNER.main_tab]


def test_task_ids_are_unique():
    ids = [task_id for _, task_id, _ in PUBLISHING_SCREENS]
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# One screen from each family, driven end to end
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(qapp):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def test_reading_run_publishes_start_cancel_and_finish(qtbot, test_config, registry):
    """A queue screen: start, Cancel marks cancelling, thread end closes it."""
    with patch("anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker") as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="ReadingWorker")
        tab = ReadingNovelsTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(tab)
        tab.bind_task_registry(registry)

        assert tab._launch_run([MagicMock(name="item")])
        snapshot = registry.snapshot("queue.reading.novels")
        assert snapshot is not None
        assert snapshot.is_running
        assert snapshot.total == 1
        assert snapshot.title == "Novel mining"

        tab._freeze_run_bar(tab.progress_widget)
        assert registry.snapshot("queue.reading.novels").cancelling

        tab._cancel_requested = True
        tab.worker_thread = None
        tab._on_worker_finished()
        closed = registry.snapshot("queue.reading.novels")
        assert not closed.is_running
        assert closed.outcome is TaskOutcome.CANCELLED

        tab.deleteLater()


def test_tool_run_publishes_progress_and_a_successful_finish(qtbot, test_config, registry):
    """A file tool: the shared slots carry counts and the terminal outcome."""
    from anki_miner.models import TerminalOutcome

    tab = CondenseTab(test_config, suppress_optional_startup=True)
    qtbot.addWidget(tab)
    tab.bind_task_registry(registry)

    tab._total_files = 2
    tab._begin_tool_run(2)
    assert registry.snapshot("tools.condense").is_running

    tab._on_file_progress(0, 40, "Extracting")
    snapshot = registry.snapshot("tools.condense")
    assert snapshot.current == 0
    assert snapshot.total == 2
    assert snapshot.detail == "Extracting"
    # Several files: the bar counts finished files, never a blend of their percentages (D18).
    assert snapshot.measured_percent is None
    assert snapshot.fraction == 0.0

    tab._on_file_finished(0, "/tmp/out.mka", None)
    assert registry.snapshot("tools.condense").current == 1

    tab._on_queue_finished(TerminalOutcome.SUCCESS)
    closed = registry.snapshot("tools.condense")
    assert not closed.is_running
    assert closed.outcome is TaskOutcome.SUCCEEDED

    tab.deleteLater()


def test_tool_cancel_marks_the_run_cancelling_not_finished(qtbot, test_config, registry):
    """D22: Cancel freezes the numbers and keeps the clock; it does not end the run."""
    tab = CondenseTab(test_config, suppress_optional_startup=True)
    qtbot.addWidget(tab)
    tab.bind_task_registry(registry)
    tab._total_files = 1
    tab._begin_tool_run(1)

    tab._on_cancel()

    snapshot = registry.snapshot("tools.condense")
    assert snapshot.cancelling
    assert snapshot.is_running
    assert snapshot.outcome is None

    tab.deleteLater()


def test_binding_also_points_the_pinned_bar_at_the_same_task(qtbot, test_config, registry):
    """W2-T5 left the bar's stage/progress/clock collapsed for want of a producer."""
    tab = CondenseTab(test_config, suppress_optional_startup=True)
    qtbot.addWidget(tab)

    tab.bind_task_registry(registry)

    assert tab.action_bar is not None
    assert tab.action_bar._task_id == "tools.condense"

    tab.deleteLater()


@pytest.fixture
def one_video_run(qtbot, test_config, registry):
    """A Video OCR run over one file, bound to the registry and started."""
    tab = VideoOcrTab(test_config, suppress_optional_startup=True)
    qtbot.addWidget(tab)
    tab.bind_task_registry(registry)
    tab._total_files = 1
    tab._begin_tool_run(1)
    yield tab
    tab.deleteLater()


def test_one_file_run_shows_the_files_measured_position(one_video_run, registry):
    """A user's two-hour scan read "Video OCR 0%" for its whole run: 0 of 1 files done.

    In a one-item run the item's measured position is the run's position, so
    nothing is blended (D18's "bar for that stage where a real denominator
    exists"). The detailed line still states the true file count.
    """
    from anki_miner.gui.utils.task_lines import format_task_line, format_task_summary

    one_video_run._on_file_progress(0, 29, "Reading subtitles: 22:49 / 1:58:05")

    snapshot = registry.snapshot("tools.videoocr")
    assert format_task_summary(snapshot) == "Video OCR 29%"
    assert "0 / 1" in format_task_line(snapshot)


def test_one_file_run_holds_its_position_through_an_unmeasured_emit(one_video_run, registry):
    """Book sync's align phase has no denominator: the bar keeps what was measured."""
    one_video_run._on_file_progress(0, 29, "Transcribing: 29%")
    one_video_run._on_file_progress(0, UNMEASURED, "Aligning to the book…")

    snapshot = registry.snapshot("tools.videoocr")
    assert snapshot.percent == 29
    assert snapshot.detail == "Aligning to the book…"


def test_one_file_run_reads_100_only_once_the_file_finishes(one_video_run, registry):
    """Workers emit "Done" at 100 before file_finished; the run is not over yet."""
    one_video_run._on_file_progress(0, 100, "Done")
    assert registry.snapshot("tools.videoocr").percent == 99

    one_video_run._on_file_finished(0, "/tmp/out.srt", None)
    assert registry.snapshot("tools.videoocr").fraction == 1.0


def test_one_file_run_follows_a_measured_step_back(one_video_run, registry):
    """A whisper.cpp window re-decoded on CPU restarts that window: show what is measured."""
    one_video_run._on_file_progress(0, 40, "Transcribing: 40%")
    one_video_run._on_file_progress(0, 10, "Transcribing: 10%")

    assert registry.snapshot("tools.videoocr").percent == 10


def test_a_new_run_starts_from_zero_not_the_last_runs_position(one_video_run, registry):
    from anki_miner.models import TerminalOutcome

    one_video_run._on_file_progress(0, 50, "Reading subtitles: 1:00 / 2:00")
    one_video_run._on_file_finished(0, "/tmp/out.srt", None)
    one_video_run._on_queue_finished(TerminalOutcome.SUCCESS)

    one_video_run._begin_tool_run(1)
    one_video_run._on_file_progress(0, UNMEASURED, "Reading subtitles: 0:01")

    assert registry.snapshot("tools.videoocr").percent == 0


def test_cancel_freezes_a_one_file_runs_position(one_video_run, registry):
    from anki_miner.gui.utils.task_lines import format_task_summary

    one_video_run._on_file_progress(0, 30, "Reading subtitles: 0:30 / 1:40")
    one_video_run._on_cancel()
    one_video_run._on_file_progress(0, 90, "Reading subtitles: 1:30 / 1:40")

    snapshot = registry.snapshot("tools.videoocr")
    assert snapshot.percent == 30
    assert "Cancelling" in format_task_summary(snapshot)


def test_unbound_screen_runs_exactly_as_before(qtbot, test_config):
    """Publishing is opt-in wiring: nothing breaks without a registry."""
    tab = CondenseTab(test_config, suppress_optional_startup=True)
    qtbot.addWidget(tab)

    tab._total_files = 1
    tab._begin_tool_run(1)
    tab._on_file_progress(0, 10, "Extracting")
    tab._on_cancel()

    assert tab._task_handle is None
    tab.deleteLater()

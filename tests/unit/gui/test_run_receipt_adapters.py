"""Every mining screen ends its run in a receipt instead of a dialog (D20).

The eight screens do not share one terminal path: the two list queues converge
on ``_after_run_cleanup``, the four Reading tabs each forward results from their
own ``_on_item_finished`` and converge on ``_apply_terminal_bar_state``, and
Single/Batch have their own worker-terminal slots. Each therefore gets its own
adapter, and each is covered here — a queue-base-only fix would leave Reading's
modal storm exactly where it was.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers.run_receipt import RunReceiptAccumulator
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.models.processing import (
    CANCELLED_ERROR,
    ProcessingResult,
    TerminalOutcome,
    WhitelistCoverage,
)
from anki_miner.models.reading import ReadingSourceRef

_READING_WORKER = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_NOVELS_DETECT = "anki_miner.gui.widgets._reading_mining_base.detector.detect"


@pytest.fixture
def clock(monkeypatch):
    """Freeze the receipt's clock; the test advances it by hand."""
    state = {"t": 1000.0}
    monkeypatch.setattr(
        MiningTabBase,
        "_receipt_now",
        staticmethod(lambda: (state["t"], state["t"])),
    )
    return state


@pytest.fixture
def task_registry(qapp):
    registry = TaskRegistry()
    yield registry
    registry.shutdown()


def _result(cards: int, *, errors: list[str] | None = None) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=cards * 3,
        new_words_found=cards,
        cards_created=cards,
        errors=list(errors or []),
        card_ids=list(range(cards)),
    )


@pytest.mark.parametrize(
    ("results", "outcome", "title"),
    [
        ([_result(2), _result(0, errors=["deck missing"])], TerminalOutcome.PARTIAL, "Finished with errors"),
        ([_result(0, errors=["deck missing"])], TerminalOutcome.FAILED, "Mining failed"),
    ],
)
def test_non_success_run_details_keep_the_terminal_header(qtbot, results, outcome, title):
    from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog

    accumulator = RunReceiptAccumulator(len(results), monotonic_start=0.0, wall_start=0.0)
    for result in results:
        accumulator.record_result(result)
    aggregate = accumulator.finish(monotonic_now=1.0, wall_now=1.0).aggregate_result()

    assert aggregate is not None
    dialog = ResultsDialog(aggregate)
    qtbot.addWidget(dialog)
    assert getattr(aggregate, "terminal_outcome", None) is outcome
    assert dialog._title_label.text() == title


# ---------------------------------------------------------------------------
# List queues (YouTube, Audiobook)
# ---------------------------------------------------------------------------


@pytest.fixture
def youtube_tab(qtbot, test_config: AnkiMinerConfig):
    cfg = replace(test_config, youtube_max_duration_s=7200, youtube_cookies_from_browser=None)
    from anki_miner.gui.widgets.youtube_tab import YouTubeTab

    with (
        patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubeProbeWorker") as probe_cls,
        patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker") as queue_cls,
    ):
        probe_cls.side_effect = lambda *a, **kw: MagicMock(name="ProbeWorker")
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = YouTubeTab(
            config=cfg,
            processor=MagicMock(name="EpisodeProcessor"),
            fetcher=MagicMock(name="Fetcher"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)
        try:
            yield widget
        finally:
            widget.deleteLater()


def _ready_youtube_item(tab, video_id: str):
    from anki_miner.models.youtube import VideoInfo

    tab.url_edit.setText(f"https://www.youtube.com/watch?v={video_id}")
    tab._on_add_clicked()
    item = tab._queue.all_items()[-1]
    tab._add_flow._on_probe_done(
        item,
        VideoInfo(
            video_id=video_id,
            title=f"Video {video_id}",
            duration_s=600,
            has_manual_ja_subs=True,
            has_auto_ja_subs=False,
            is_live=False,
            is_age_restricted=False,
        ),
    )
    return item


class TestListQueueReceipt:
    def test_a_finished_queue_run_leaves_a_receipt(self, youtube_tab, clock):
        _ready_youtube_item(youtube_tab, "aaa")
        _ready_youtube_item(youtube_tab, "bbb")
        youtube_tab._on_mine_clicked()

        youtube_tab._on_item_finished(0, _result(30), None, 1)
        youtube_tab._on_item_finished(1, _result(12), None, 1)
        clock["t"] += 95
        youtube_tab._after_run_cleanup()

        assert youtube_tab._receipt_widget.summary_text == ("Mining complete — 2 videos, 42 notes added in 01m 35s")
        assert youtube_tab._receipt_widget.isVisibleTo(youtube_tab) is True

    def test_a_cancelled_queue_run_keeps_the_work_it_did(self, youtube_tab, clock):
        _ready_youtube_item(youtube_tab, "aaa")
        _ready_youtube_item(youtube_tab, "bbb")
        youtube_tab._on_mine_clicked()

        youtube_tab._on_item_finished(0, _result(9), None, 1)
        youtube_tab._on_item_finished(1, _result(0, errors=[CANCELLED_ERROR]), None, 1)
        youtube_tab._cancel_requested = True
        clock["t"] += 20
        youtube_tab._after_run_cleanup()

        assert youtube_tab._receipt_widget.summary_text == (
            "Cancelled — 1 of 2 videos completed; 9 notes added in 00m 20s"
        )

    def test_the_next_run_clears_the_previous_receipt(self, youtube_tab, clock):
        _ready_youtube_item(youtube_tab, "aaa")
        youtube_tab._on_mine_clicked()
        youtube_tab._on_item_finished(0, _result(4), None, 1)
        youtube_tab._after_run_cleanup()
        youtube_tab.worker_thread = None
        assert youtube_tab._receipt_widget.summary_text != ""

        _ready_youtube_item(youtube_tab, "bbb")
        youtube_tab._on_mine_clicked()

        assert youtube_tab._receipt_widget.summary_text == ""
        assert youtube_tab._receipt_widget.isVisibleTo(youtube_tab) is False

    def test_no_item_result_reaches_the_window_as_a_dialog(self, youtube_tab, clock):
        """The presenter still hears every item; the window no longer opens one."""
        _ready_youtube_item(youtube_tab, "aaa")
        youtube_tab._on_mine_clicked()

        youtube_tab._on_item_finished(0, _result(4), None, 1)

        assert youtube_tab._presenter.show_run_details.called is False

    def test_the_whitelist_line_lands_in_the_screen_log(self, youtube_tab, clock):
        """The queue screens' presenters route info/warning to the window's
        transient status bar, not to this log. The report is the record the
        user comes back for, so it is written to the log directly."""
        _ready_youtube_item(youtube_tab, "aaa")
        youtube_tab._on_mine_clicked()
        result = _result(1)
        result.whitelist_coverage = WhitelistCoverage(frozenset({"食べる", "走る"}), mined=frozenset({"食べる"}))

        youtube_tab._on_item_finished(0, result, None, 1)
        clock["t"] += 5
        youtube_tab._after_run_cleanup()

        assert youtube_tab._receipt_widget.summary_text == (
            "Mining complete — 1 notes added in 00m 05s · Whitelist: 1 of 2 mined"
        )
        assert "Whitelist: 1 of 2 mined. Not mined: 走る." in youtube_tab.log_widget.full_text()

    def test_view_details_forwards_the_whole_run(self, youtube_tab, clock):
        _ready_youtube_item(youtube_tab, "aaa")
        _ready_youtube_item(youtube_tab, "bbb")
        youtube_tab._on_mine_clicked()
        youtube_tab._on_item_finished(0, _result(2), None, 1)
        youtube_tab._on_item_finished(1, _result(3), None, 1)
        youtube_tab._after_run_cleanup()

        youtube_tab._receipt_widget.details_button.click()

        aggregate = youtube_tab._presenter.show_run_details.call_args.args[0]
        assert aggregate.cards_created == 5

    def test_cancelled_details_keep_the_cancelled_header(self, youtube_tab, clock, qtbot):
        from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog

        _ready_youtube_item(youtube_tab, "aaa")
        _ready_youtube_item(youtube_tab, "bbb")
        youtube_tab._on_mine_clicked()
        youtube_tab._on_item_finished(0, _result(2), None, 1)
        youtube_tab._cancel_requested = True
        youtube_tab._after_run_cleanup()

        youtube_tab._receipt_widget.details_button.click()

        aggregate = youtube_tab._presenter.show_run_details.call_args.args[0]
        dialog = ResultsDialog(aggregate)
        qtbot.addWidget(dialog)
        assert getattr(aggregate, "terminal_outcome", None) is TerminalOutcome.CANCELLED
        assert dialog._title_label.text() == "Cancelled"


# ---------------------------------------------------------------------------
# Reading (novels stands in for the four; each is smoke-checked below)
# ---------------------------------------------------------------------------


@pytest.fixture
def novels_tab(qtbot, test_config: AnkiMinerConfig):
    from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab

    with patch(_READING_WORKER) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = ReadingNovelsTab(
            config=test_config,
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)
        try:
            yield widget
        finally:
            widget.deleteLater()


def _start_novel_run(tab, tmp_path):
    book = tmp_path / "book.epub"
    book.write_text("dummy", encoding="utf-8")
    tab.book_selector.set_path(str(book))
    with patch(_NOVELS_DETECT, return_value=[ReadingSourceRef(kind="epub", path=book, title="Book")]):
        tab._on_mine_clicked()


class TestReadingReceipt:
    def test_a_finished_book_leaves_a_receipt(self, novels_tab, clock, tmp_path):
        _start_novel_run(novels_tab, tmp_path)

        novels_tab._on_item_finished(0, _result(11), None, 1)
        clock["t"] += 63
        novels_tab._after_run_cleanup()

        assert novels_tab._receipt_widget.summary_text == "Mining complete — 11 notes added in 01m 03s"

    def test_a_cancelled_book_reports_zero_without_pretending_it_finished(self, novels_tab, clock, tmp_path):
        _start_novel_run(novels_tab, tmp_path)

        novels_tab._on_item_finished(0, _result(0, errors=[CANCELLED_ERROR]), None, 1)
        novels_tab._cancel_requested = True
        clock["t"] += 8
        novels_tab._after_run_cleanup()

        assert novels_tab._receipt_widget.summary_text == "Cancelled — 0 notes added in 00m 08s"

    def test_a_failed_book_says_so(self, novels_tab, clock, tmp_path):
        _start_novel_run(novels_tab, tmp_path)

        novels_tab._on_item_finished(0, None, "epub is DRM-protected", 1)
        clock["t"] += 3
        novels_tab._after_run_cleanup()

        assert novels_tab._receipt_widget.summary_text == "Mining failed — 0 notes added in 00m 03s"

    def test_a_multi_file_reading_run_counts_in_that_screens_own_noun(self, qtbot, test_config, clock, tmp_path):
        from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab

        with patch(_READING_WORKER) as queue_cls:
            queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            tab = ReadingSubtitlesTab(
                config=test_config,
                processor=MagicMock(name="EpisodeProcessor"),
                presenter=MagicMock(name="Presenter"),
            )
            qtbot.addWidget(tab)
            for name in ("ep01.srt", "ep02.srt"):
                path = tmp_path / name
                path.write_text("1\n00:00:01,000 --> 00:00:02,000\n本\n", encoding="utf-8")
                tab._add_paths([path])
            tab._on_mine_clicked()

        tab._on_item_finished(0, _result(5), None, 1)
        tab._on_item_finished(1, _result(4), None, 1)
        clock["t"] += 74
        tab._after_run_cleanup()

        assert tab._receipt_widget.summary_text == "Mining complete — 2 subtitle files, 9 notes added in 01m 14s"
        tab.deleteLater()


# ---------------------------------------------------------------------------
# Single episode
# ---------------------------------------------------------------------------


@pytest.fixture
def single_tab(qtbot, test_config):
    from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab

    widget = SingleEpisodeTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def _start_single_run(tab, tmp_path):
    video = tmp_path / "ep01.mkv"
    video.touch()
    subs = tmp_path / "ep01.ass"
    subs.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)
    with (
        patch("anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=MagicMock()),
        patch("anki_miner.gui.widgets.single_episode_tab.create_episode_processor", return_value=MagicMock()),
    ):
        tab._start_processing()


class TestSingleEpisodeReceipt:
    def test_a_finished_episode_leaves_a_receipt(self, single_tab, clock, tmp_path):
        _start_single_run(single_tab, tmp_path)

        single_tab._on_processing_finished(_result(24))
        clock["t"] += 137
        single_tab._on_run_thread_finished()

        assert single_tab._receipt_widget.summary_text == "Mining complete — 24 notes added in 02m 17s"

    def test_a_cancelled_episode_that_already_wrote_notes_keeps_them(self, single_tab, clock, tmp_path):
        """The worker still emits a result when notes reached Anki before the stop."""
        _start_single_run(single_tab, tmp_path)
        single_tab._cancel_requested = True

        single_tab._on_processing_finished(_result(6))
        clock["t"] += 41
        single_tab._on_run_thread_finished()

        assert single_tab._receipt_widget.summary_text == "Cancelled — 6 notes added in 00m 41s"

    def test_a_worker_error_leaves_a_failure_receipt(self, single_tab, clock, tmp_path):
        _start_single_run(single_tab, tmp_path)

        single_tab._on_processing_error("ffprobe not found")
        clock["t"] += 2
        single_tab._on_run_thread_finished()

        assert single_tab._receipt_widget.summary_text == "Mining failed — 0 notes added in 00m 02s"

    def test_a_leaked_previous_worker_cannot_seal_the_live_run(self, single_tab, clock, tmp_path):
        """A timed-out teardown leaves an old thread that finishes mid-new-run."""
        _start_single_run(single_tab, tmp_path)
        stale_worker = MagicMock(name="LeakedWorker")
        single_tab.sender = MagicMock(return_value=stale_worker)

        single_tab._on_run_thread_finished()

        assert single_tab._receipt_widget.summary_text == ""
        assert single_tab._receipt_widget.isVisibleTo(single_tab) is False


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------


@pytest.fixture
def batch_tab(qtbot, test_config):
    from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab

    widget = BatchProcessingTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


class TestBatchReceipt:
    def test_the_quick_path_reports_the_whitelist_once_at_run_end(self, batch_tab, clock):
        entries = frozenset({"食べる", "走る"})
        first = _result(2)
        first.whitelist_coverage = WhitelistCoverage(entries, mined=frozenset({"食べる"}))
        second = _result(1)
        second.whitelist_coverage = WhitelistCoverage(entries, known=frozenset({"食べる"}))
        with patch("anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread", MagicMock()):
            batch_tab._start_processing_with_pairs([object(), object()])
        batch_tab._on_processing_finished([first, second])
        clock["t"] += 30
        batch_tab._on_run_thread_finished()

        assert batch_tab._receipt_widget.summary_text == (
            "Mining complete — 2 episodes, 3 notes added in 00m 30s · Whitelist: 1 of 2 mined"
        )
        line = "Whitelist: 1 of 2 mined. Not mined: 走る."
        assert line in batch_tab.log_widget.full_text()
        # Sealed once: a second terminal signal must not log the line again.
        batch_tab._on_run_thread_finished()
        assert batch_tab.log_widget.full_text().count(line) == 1

    def test_the_quick_path_ends_in_a_receipt_and_no_message_box(self, batch_tab, clock):
        with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox") as message_box:
            with patch("anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread", MagicMock()):
                batch_tab._start_processing_with_pairs([object(), object()])
            batch_tab._on_processing_finished([_result(20), _result(13)])
            clock["t"] += 3612
            batch_tab._on_run_thread_finished()

        assert batch_tab._receipt_widget.summary_text == ("Mining complete — 2 episodes, 33 notes added in 1h 00m 12s")
        message_box.information.assert_not_called()
        message_box.warning.assert_not_called()

    def test_a_partly_failed_quick_run_names_the_failures_without_a_dialog(self, batch_tab, clock):
        with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox") as message_box:
            with patch("anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread", MagicMock()):
                batch_tab._start_processing_with_pairs([object(), object()])
            batch_tab._on_processing_finished([_result(5), _result(0, errors=["deck missing"])])
            clock["t"] += 30
            batch_tab._on_run_thread_finished()

        assert batch_tab._receipt_widget.summary_text == (
            "Finished with errors — 1 of 2 episodes completed; 5 notes added in 00m 30s"
        )
        message_box.warning.assert_not_called()

    def test_a_cancelled_quick_run_congratulates_nobody(self, batch_tab, clock):
        """The old box fired after a cancellation too. This is the exact case."""
        with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox") as message_box:
            with patch("anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread", MagicMock()):
                batch_tab._start_processing_with_pairs([object(), object(), object()])
            batch_tab._cancel_requested = True
            batch_tab._on_processing_finished([_result(7)])
            clock["t"] += 497
            batch_tab._on_run_thread_finished()

        assert batch_tab._receipt_widget.summary_text == (
            "Cancelled — 1 of 3 episodes completed; 7 notes added in 08m 17s"
        )
        message_box.information.assert_not_called()

    def test_the_queue_path_reports_item_failure_consistently(self, batch_tab, clock, task_registry, tmp_path):
        batch_tab.bind_task_registry(task_registry)
        batch_tab.batch_queue.add_item(tmp_path, tmp_path, "Show A", 0.0)
        batch_tab.batch_queue.add_item(tmp_path, tmp_path, "Show B", 0.0)
        with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox") as message_box:
            with patch("anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread", MagicMock()):
                batch_tab._start_queue_worker()
            batch_tab._on_item_completed(batch_tab.batch_queue.get_all_items()[0].id, 40)
            batch_tab._on_item_failed(batch_tab.batch_queue.get_all_items()[1].id, "no subtitles", 5)
            clock["t"] += 65
            batch_tab._on_queue_finished(40)
            batch_tab._on_run_thread_finished()

        assert batch_tab._receipt_widget.summary_text == (
            "Finished with errors — 1 of 2 series completed; 45 notes added in 01m 05s"
        )
        assert batch_tab.overall_progress_widget.status_label.text() == "Finished with errors — see log"
        assert batch_tab._receipt_widget.receipt.outcome is TerminalOutcome.PARTIAL
        assert task_registry.snapshot(batch_tab.TASK_ID).outcome is TaskOutcome.FAILED
        message_box.information.assert_not_called()

    def test_a_cancelled_queue_run_opens_no_dialog(self, batch_tab, clock, tmp_path):
        batch_tab.batch_queue.add_item(tmp_path, tmp_path, "Show A", 0.0)
        with patch("anki_miner.gui.widgets.batch_processing_tab.QMessageBox") as message_box:
            with patch("anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread", MagicMock()):
                batch_tab._start_queue_worker()
            batch_tab._cancel_requested = True
            clock["t"] += 11
            batch_tab._on_queue_finished(0)
            batch_tab._on_run_thread_finished()

        assert batch_tab._receipt_widget.summary_text == "Cancelled — 0 notes added in 00m 11s"
        message_box.information.assert_not_called()


def _assert_receipt_follows(tab, anchor_name: str) -> None:
    """The receipt is the next thing in the progress bar's own layout."""
    anchor = getattr(tab, anchor_name)
    layout = anchor.parentWidget().layout()
    assert layout.itemAt(layout.indexOf(anchor) + 1).widget() is tab._receipt_widget


@pytest.mark.parametrize(
    ("module", "cls_name", "anchor"),
    [
        ("reading_manga_tab", "ReadingMangaTab", "overall_progress_widget"),
        ("reading_novels_tab", "ReadingNovelsTab", "progress_widget"),
        ("reading_subtitles_tab", "ReadingSubtitlesTab", "overall_progress_widget"),
        ("reading_text_tab", "ReadingTextTab", "overall_progress_widget"),
    ],
)
def test_every_reading_tab_installs_a_receipt_under_its_progress_bar(qtbot, test_config, module, cls_name, anchor):
    import importlib

    tab_cls = getattr(importlib.import_module(f"anki_miner.gui.widgets.{module}"), cls_name)
    with patch(_READING_WORKER):
        widget = tab_cls(
            config=test_config,
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)

    _assert_receipt_follows(widget, anchor)
    widget.deleteLater()


def test_the_youtube_receipt_sits_under_its_progress_bar(youtube_tab):
    _assert_receipt_follows(youtube_tab, "progress_widget")


def test_the_single_episode_receipt_sits_under_its_progress_bar(single_tab):
    _assert_receipt_follows(single_tab, "progress_widget")


def test_the_batch_receipt_sits_under_its_progress_bar(batch_tab):
    _assert_receipt_follows(batch_tab, "overall_progress_widget")


def test_the_audiobook_receipt_sits_under_its_progress_bar(qtbot, test_config):
    from anki_miner.gui.widgets.audiobook_tab import AudiobookTab

    with patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker"):
        widget = AudiobookTab(
            config=test_config,
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)

    _assert_receipt_follows(widget, "progress_widget")
    widget.deleteLater()

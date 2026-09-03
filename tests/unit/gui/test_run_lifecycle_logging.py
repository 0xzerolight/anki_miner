"""Every mining run says in the log that it started, how it ended, or why it never began.

"I pressed Mine and nothing happened" is unanswerable from a log that records
only the work a run did. The three lifecycle anchors here close that: ``Run
start`` names the screen and the options the run was launched with, ``Run
refused`` names the reason a launch returned without a worker, and ``Run end``
restates the receipt the user saw so a support report and the log can be
reconciled. ``Run control`` covers the buttons that change a live run, which are
the other half of "nothing happened".

``_begin_receipt`` / ``_finish_receipt`` are the single pair every one of the
eight mining screens passes through, so the two lifecycle lines are asserted
there and only the per-screen field payloads are checked per tab.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.models.processing import ProcessingResult
from anki_miner.models.reading import ReadingSourceRef

_WIDGETS_LOGGER = "anki_miner.gui.widgets"
_READING_WORKER = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"


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
def lifecycle_log(caplog):
    """Capture the whole ``gui.widgets`` namespace at INFO, restoring the level."""
    with caplog.at_level(logging.INFO, logger=_WIDGETS_LOGGER):
        yield caplog


def _messages(caplog, anchor: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(anchor)]


def _one(caplog, anchor: str) -> str:
    found = _messages(caplog, anchor)
    assert len(found) == 1, f"expected exactly one {anchor!r} line, got {found}"
    return found[0]


def _result(cards: int) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=cards * 3,
        new_words_found=cards,
        cards_created=cards,
        card_ids=list(range(cards)),
    )


# ---------------------------------------------------------------------------
# YouTube stands in for the six queue screens (one shared _launch_run)
# ---------------------------------------------------------------------------


@pytest.fixture
def youtube_tab(qtbot, test_config: AnkiMinerConfig):
    from dataclasses import replace

    from anki_miner.gui.widgets.youtube_tab import YouTubeTab

    cfg = replace(test_config, youtube_max_duration_s=7200, youtube_cookies_from_browser=None)
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


class TestRunStart:
    def test_a_launched_queue_run_names_the_screen_and_its_items(self, youtube_tab, lifecycle_log):
        _ready_youtube_item(youtube_tab, "aaa")
        _ready_youtube_item(youtube_tab, "bbb")

        youtube_tab._on_mine_clicked()

        line = _one(lifecycle_log, "Run start:")
        assert "screen=queue.youtube" in line
        assert "items=2" in line
        assert "review_words=False" in line
        assert "first=aaa,bbb" in line

    def test_a_screen_without_a_receipt_widget_still_records_the_start(self, youtube_tab, lifecycle_log):
        """Deck Builder installs no receipt; the run still has to be in the log."""
        youtube_tab._receipt_widget = None

        youtube_tab._begin_receipt(3, run_fields={"deck": "Mining"})

        line = _one(lifecycle_log, "Run start:")
        assert "screen=queue.youtube items=3 deck=Mining" in line


class TestRunRefused:
    def test_an_empty_run_is_refused_by_name(self, youtube_tab, lifecycle_log):
        assert youtube_tab._launch_run([]) is False

        line = _one(lifecycle_log, "Run refused:")
        assert "screen=queue.youtube reason=no_items" in line
        record = next(r for r in lifecycle_log.records if r.getMessage().startswith("Run refused:"))
        assert record.levelno == logging.WARNING

    def test_a_busy_screen_names_the_worker_holding_it(self, youtube_tab, lifecycle_log):
        item = _ready_youtube_item(youtube_tab, "aaa")
        youtube_tab.worker_thread = MagicMock(name="QueueWorker")

        try:
            assert youtube_tab._launch_run([item]) is False
        finally:
            youtube_tab.worker_thread = None

        line = _one(lifecycle_log, "Run refused:")
        assert "reason=worker_busy" in line
        assert "busy=MagicMock" in line

    def test_a_run_with_no_presenter_to_build_a_processor_says_so(self, youtube_tab, lifecycle_log):
        item = _ready_youtube_item(youtube_tab, "aaa")
        youtube_tab._processor = None
        youtube_tab._presenter = None

        assert youtube_tab._launch_run([item]) is False

        assert "reason=no_presenter" in _one(lifecycle_log, "Run refused:")


class TestRunEnd:
    def test_the_end_line_restates_the_receipt(self, youtube_tab, lifecycle_log, clock):
        _ready_youtube_item(youtube_tab, "aaa")
        _ready_youtube_item(youtube_tab, "bbb")
        youtube_tab._on_mine_clicked()
        youtube_tab._on_item_finished(0, _result(30), None, 1)
        youtube_tab._on_item_finished(1, _result(12), None, 1)
        clock["t"] += 95

        youtube_tab._after_run_cleanup()

        line = _one(lifecycle_log, "Run end:")
        assert "screen=queue.youtube" in line
        assert "outcome=success" in line
        assert "items_total=2 completed=2 failed=0" in line
        assert "notes=42 note_ids=42" in line
        assert "active_s=95.0 wall_s=95.0" in line

    def test_a_cancelled_run_reports_the_cancelled_outcome(self, youtube_tab, lifecycle_log, clock):
        _ready_youtube_item(youtube_tab, "aaa")
        youtube_tab._on_mine_clicked()
        youtube_tab._cancel_requested = True
        clock["t"] += 8

        youtube_tab._after_run_cleanup()

        assert "outcome=cancelled" in _one(lifecycle_log, "Run end:")

    def test_a_second_terminal_signal_does_not_log_a_second_end(self, youtube_tab, lifecycle_log, clock):
        _ready_youtube_item(youtube_tab, "aaa")
        youtube_tab._on_mine_clicked()
        youtube_tab._on_item_finished(0, _result(1), None, 1)

        youtube_tab._after_run_cleanup()
        youtube_tab._finish_receipt()

        assert len(_messages(lifecycle_log, "Run end:")) == 1


class TestRunControls:
    @pytest.mark.parametrize(
        ("method", "action"),
        [
            ("_on_stop_all_clicked", "stop"),
            ("_on_pause_requested", "pause"),
            ("_on_resume_requested", "resume"),
            ("_on_finish_current_requested", "finish_current"),
        ],
    )
    def test_every_queue_control_records_the_verb(self, youtube_tab, lifecycle_log, method, action):
        _ready_youtube_item(youtube_tab, "aaa")
        youtube_tab._on_mine_clicked()
        lifecycle_log.clear()

        getattr(youtube_tab, method)()

        line = _one(lifecycle_log, "Run control:")
        assert f"screen=queue.youtube action={action}" in line
        assert "worker=" in line

    def test_a_control_pressed_with_no_run_is_still_recorded(self, youtube_tab, lifecycle_log):
        """The 'I pressed Stop and nothing happened' case, which used to be silent."""
        youtube_tab._on_stop_all_clicked()

        line = _one(lifecycle_log, "Run control:")
        assert "action=stop worker=-" in line

    def test_a_run_level_fatal_is_logged_as_such(self, youtube_tab, lifecycle_log):
        youtube_tab._on_run_error("dictionary index is stale")

        line = _one(lifecycle_log, "Run fatal:")
        assert "screen=queue.youtube" in line
        assert "dictionary index is stale" in line
        record = next(r for r in lifecycle_log.records if r.getMessage().startswith("Run fatal:"))
        assert record.levelno == logging.ERROR


# ---------------------------------------------------------------------------
# Reading (a second queue screen: the shared fields must not be YouTube-shaped)
# ---------------------------------------------------------------------------


def test_a_reading_run_names_its_own_screen_and_titles(qtbot, test_config, lifecycle_log, tmp_path):
    from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab

    with patch(_READING_WORKER) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        tab = ReadingNovelsTab(
            config=test_config,
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(tab)
        book = tmp_path / "book.epub"
        book.write_text("dummy", encoding="utf-8")
        tab.book_selector.set_path(str(book))
        with patch(
            "anki_miner.gui.widgets._reading_mining_base.detector.detect",
            return_value=[ReadingSourceRef(kind="epub", path=book, title="Book")],
        ):
            tab._on_mine_clicked()

    line = _one(lifecycle_log, "Run start:")
    assert "screen=queue.reading.novels items=1" in line
    assert "first=Book" in line
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
    return video, subs


class TestSingleEpisodeRunFields:
    def test_the_start_line_carries_the_options_the_run_used(self, single_tab, lifecycle_log, tmp_path):
        video, subs = _start_single_run(single_tab, tmp_path)

        line = _one(lifecycle_log, "Run start:")
        assert "screen=run.single items=1" in line
        assert f"video={video}" in line
        assert f"subtitle={subs}" in line
        assert "secondary=- secondary_offset=0.0" in line
        assert "offset=0.0" in line
        assert "audio_track=-" in line
        assert "source_label=ep01" in line
        assert f"deck={single_tab.config.anki_deck_name}" in line
        assert f"note_type={single_tab.config.anki_note_type}" in line
        assert "language=ja" in line
        assert "review_words=False" in line

    def test_cancel_is_recorded_as_a_run_control(self, single_tab, lifecycle_log, tmp_path):
        _start_single_run(single_tab, tmp_path)
        lifecycle_log.clear()

        single_tab._on_cancel_clicked()

        line = _one(lifecycle_log, "Run control:")
        assert "screen=run.single action=cancel" in line


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


class TestBatchRunFields:
    def test_the_quick_path_names_the_pairs_it_launched_with(self, batch_tab, lifecycle_log, tmp_path):
        from anki_miner.utils.file_pairing import FilePair

        pairs = [
            FilePair(video=tmp_path / "ep01.mkv", subtitle=tmp_path / "ep01.srt"),
            FilePair(video=tmp_path / "ep02.mkv", subtitle=tmp_path / "ep02.srt"),
        ]
        with patch("anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread", MagicMock()):
            batch_tab._start_processing_with_pairs(pairs)

        line = _one(lifecycle_log, "Run start:")
        assert "screen=run.batch items=2" in line
        assert "pairs=2" in line
        assert "first=ep01,ep02" in line
        assert "video_folder=" in line
        assert "subtitle_folder=" in line

    def test_the_queue_path_names_the_series_it_launched_with(self, batch_tab, lifecycle_log, tmp_path):
        batch_tab.batch_queue.add_item(tmp_path, tmp_path, "Show A", 0.0)
        with patch("anki_miner.gui.workers.batch_queue_worker.BatchQueueWorkerThread", MagicMock()):
            batch_tab._start_queue_worker()

        line = _one(lifecycle_log, "Run start:")
        assert "screen=run.batch items=1" in line
        assert 'first="Show A"' in line

    def test_cancel_is_recorded_as_a_run_control(self, batch_tab, lifecycle_log, tmp_path):
        from anki_miner.utils.file_pairing import FilePair

        with patch("anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread", MagicMock()):
            batch_tab._start_processing_with_pairs([FilePair(video=Path("a.mkv"), subtitle=Path("a.srt"))])
        lifecycle_log.clear()

        batch_tab._on_cancel_clicked()

        assert "screen=run.batch action=cancel" in _one(lifecycle_log, "Run control:")

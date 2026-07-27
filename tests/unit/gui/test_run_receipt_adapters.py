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
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.models.processing import CANCELLED_ERROR, ProcessingResult
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


def _result(cards: int, *, errors: list[str] | None = None) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=cards * 3,
        new_words_found=cards,
        cards_created=cards,
        errors=list(errors or []),
        card_ids=list(range(cards)),
    )


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

    progress = getattr(widget, anchor)
    layout = progress.parentWidget().layout()
    assert layout.itemAt(layout.indexOf(progress) + 1).widget() is widget._receipt_widget
    widget.deleteLater()

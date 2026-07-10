"""Tests for the subtitles sub-tab of the Reading tab.

``ReadingSubtitlesTab`` mines the listed subtitle files sequentially over the
shared ``_ReadingMiningTabBase`` lifecycle — one ephemeral ``ReadingQueueItem``
per file, composed into a single whole-run progress bar (the manga pattern).
Behaviour under test:

* File-list management: Add (deduped), Remove Selected, Clear; drops append
  ALL dropped subtitle files; manga/novel drops earn a cross-tab hint.
* Start: each listed file is classified by ``detect`` into one ephemeral item;
  the whole list launches as one run. Preview is gated to exactly one listed
  file (the worker preview dialog fires once per file — N would stack).
* Per-item signals are READ-ONLY on item state (the worker owns the lifecycle):
  they compose the whole-run bar + log outcomes, never write status.
* Cleanup restores the Cancel button and the progress bar on every exit path.
* Curation context inherits the base ``(None, None)`` — table-only.

Qt threads are never started — ``ReadingQueueWorker`` is class-level patched at
the base module so ``start()`` is a no-op and constructor kwargs can be
inspected.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.services.reading.models import ReadingSourceRef

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_DETECT = "anki_miner.gui.widgets._reading_mining_base.detector.detect"
_URLS = "anki_miner.gui.widgets.reading_subtitles_tab.urls_from_event"


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig):
    """Instantiate a ReadingSubtitlesTab with the queue worker class patched."""
    with patch(_WORKER_TARGET, autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")

        widget = ReadingSubtitlesTab(
            config=test_config,
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)
        widget._queue_worker_cls = queue_cls  # type: ignore[attr-defined]
        try:
            yield widget
        finally:
            widget.deleteLater()


def _make_ref(title: str = "Ep01") -> ReadingSourceRef:
    return ReadingSourceRef(
        kind="subtitle",
        path=Path(f"/src/{title}.srt"),
        image_root=None,
        title=title,
        volume=None,
    )


def _url(local_path: str):
    """A fake QUrl whose toLocalFile returns *local_path*."""
    u = MagicMock()
    u.toLocalFile.return_value = local_path
    return u


def _sub_file(tmp_path: Path, name: str = "Ep01.srt") -> Path:
    sub = tmp_path / name
    sub.write_text("dummy", encoding="utf-8")
    return sub


def _mine(tab, paths: list[Path], *, preview: bool = False):
    """List *paths*, patch ``detect`` to one subtitle ref per path, click."""
    tab._add_paths(paths)
    with patch(_DETECT, side_effect=lambda p: [_make_ref(Path(p).stem)]):
        if preview:
            tab._on_preview_clicked()
        else:
            tab._on_mine_clicked()


class TestInitialState:
    """Idle tab: Preview/Mine visible, Cancel hidden, empty list."""

    def test_buttons_idle(self, tab):
        assert not tab.preview_button.isHidden()
        assert not tab.mine_button.isHidden()
        assert tab.cancel_button.isHidden()
        assert tab.worker_thread is None

    def test_review_checkbox_default_unchecked(self, tab):
        assert tab.review_words_checkbox.isChecked() is False

    def test_list_empty_preview_disabled(self, tab):
        assert tab.file_list.count() == 0
        assert not tab.preview_button.isEnabled()
        assert tab.mine_button.isEnabled()

    def test_section_header_says_subtitle_files(self, tab):
        from anki_miner.gui.widgets.enhanced import SectionHeader

        headers = tab.findChildren(SectionHeader)
        assert any(h.title_label.text() == "Subtitle Files" for h in headers)


class TestFileList:
    """Add/Remove/Clear list management with dedupe."""

    def test_add_paths_appends_in_order(self, tab, tmp_path):
        a, b = _sub_file(tmp_path, "a.srt"), _sub_file(tmp_path, "b.ass")
        tab._add_paths([a, b])
        assert tab.listed_paths() == [a, b]

    def test_add_paths_dedupes(self, tab, tmp_path):
        a = _sub_file(tmp_path, "a.srt")
        tab._add_paths([a])
        tab._add_paths([a, a])
        assert tab.listed_paths() == [a]

    def test_exactly_one_file_enables_preview(self, tab, tmp_path):
        tab._add_paths([_sub_file(tmp_path, "a.srt")])
        assert tab.preview_button.isEnabled()
        tab._add_paths([_sub_file(tmp_path, "b.srt")])
        assert not tab.preview_button.isEnabled()

    def test_remove_selected(self, tab, tmp_path):
        a, b = _sub_file(tmp_path, "a.srt"), _sub_file(tmp_path, "b.srt")
        tab._add_paths([a, b])
        tab.file_list.setCurrentRow(0)
        tab._on_remove_selected_clicked()
        assert tab.listed_paths() == [b]

    def test_clear(self, tab, tmp_path):
        tab._add_paths([_sub_file(tmp_path, "a.srt")])
        tab._on_clear_clicked()
        assert tab.listed_paths() == []


class TestDragDrop:
    """Tab-level drops: subtitles append (all of them), other kinds hint."""

    def test_drop_appends_all_subtitle_files(self, tab, tmp_path):
        a, b = _sub_file(tmp_path, "a.srt"), _sub_file(tmp_path, "b.vtt")
        event = MagicMock()
        with patch(_URLS, return_value=[_url(str(a)), _url(str(b))]):
            tab.dropEvent(event)
        assert tab.listed_paths() == [a, b]
        event.acceptProposedAction.assert_called_once()

    def test_drop_dedupes_against_list(self, tab, tmp_path):
        a = _sub_file(tmp_path, "a.srt")
        tab._add_paths([a])
        event = MagicMock()
        with patch(_URLS, return_value=[_url(str(a))]):
            tab.dropEvent(event)
        assert tab.listed_paths() == [a]

    def test_manga_drop_hints(self, tab, tmp_path):
        vol = tmp_path / "vol1.mokuro"
        event = MagicMock()
        with patch(_URLS, return_value=[_url(str(vol))]):
            tab.dropEvent(event)
        assert tab.listed_paths() == []
        assert "Manga tab" in tab.log_widget.text_edit.toPlainText()

    def test_novel_drop_hints(self, tab, tmp_path):
        book = tmp_path / "book.epub"
        event = MagicMock()
        with patch(_URLS, return_value=[_url(str(book))]):
            tab.dropEvent(event)
        assert tab.listed_paths() == []
        assert "Novels tab" in tab.log_widget.text_edit.toPlainText()

    def test_drag_enter_accepts_subtitle(self, tab, tmp_path):
        event = MagicMock()
        with patch(_URLS, return_value=[_url(str(tmp_path / "a.srt"))]):
            tab.dragEnterEvent(event)
        event.acceptProposedAction.assert_called_once()

    def test_drag_enter_accepts_foreign_kinds_for_hinting(self, tab, tmp_path):
        event = MagicMock()
        with patch(_URLS, return_value=[_url(str(tmp_path / "book.epub"))]):
            tab.dragEnterEvent(event)
        event.acceptProposedAction.assert_called_once()

    def test_drag_enter_ignores_unrelated(self, tab, tmp_path):
        event = MagicMock()
        with patch(_URLS, return_value=[_url(str(tmp_path / "movie.mp4"))]):
            tab.dragEnterEvent(event)
        event.acceptProposedAction.assert_not_called()

    def test_list_widget_drops_disabled(self, tab):
        # QListWidget must not swallow drops before the tab handler sees them.
        assert tab.file_list.acceptDrops() is False


class TestStartRun:
    """The listed files launch as one sequential run of ephemeral items."""

    def test_mine_builds_one_item_per_file(self, tab, tmp_path):
        queue_cls = tab._queue_worker_cls
        _mine(tab, [_sub_file(tmp_path, "Ep01.srt"), _sub_file(tmp_path, "Ep02.srt")])

        assert queue_cls.call_count == 1
        items = queue_cls.call_args.kwargs["items"]
        assert [i.title for i in items] == ["Ep01", "Ep02"]
        assert all(i.kind == "subtitle" for i in items)
        assert tab.worker_thread is not None
        tab.worker_thread.start.assert_called_once()

    def test_mine_preview_flag_false(self, tab, tmp_path):
        queue_cls = tab._queue_worker_cls
        _mine(tab, [_sub_file(tmp_path)])
        assert queue_cls.call_args.kwargs["preview_mode"] is False

    def test_preview_single_file_flag_true(self, tab, tmp_path):
        queue_cls = tab._queue_worker_cls
        _mine(tab, [_sub_file(tmp_path)], preview=True)
        assert queue_cls.call_args.kwargs["preview_mode"] is True

    def test_preview_refused_with_multiple_files(self, tab, tmp_path):
        queue_cls = tab._queue_worker_cls
        _mine(tab, [_sub_file(tmp_path, "a.srt"), _sub_file(tmp_path, "b.srt")], preview=True)
        queue_cls.assert_not_called()
        assert "exactly one" in tab.log_widget.text_edit.toPlainText()

    def test_empty_list_warns_no_run(self, tab):
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()
        queue_cls.assert_not_called()
        assert "at least one" in tab.log_widget.text_edit.toPlainText()

    def test_missing_file_warns_no_run(self, tab, tmp_path):
        queue_cls = tab._queue_worker_cls
        tab._add_paths([tmp_path / "ghost.srt"])
        tab._on_mine_clicked()
        queue_cls.assert_not_called()
        assert "not found" in tab.log_widget.text_edit.toPlainText()

    def test_detect_error_surfaced_no_run(self, tab, tmp_path):
        queue_cls = tab._queue_worker_cls
        tab._add_paths([_sub_file(tmp_path)])
        with patch(_DETECT, side_effect=SetupError("No subtitle cues found in 'Ep01.srt'")):
            tab._on_mine_clicked()
        queue_cls.assert_not_called()
        assert "No subtitle cues" in tab.log_widget.text_edit.toPlainText()

    def test_run_refused_while_worker_active(self, tab, tmp_path):
        queue_cls = tab._queue_worker_cls
        _mine(tab, [_sub_file(tmp_path)])
        assert queue_cls.call_count == 1
        _mine(tab, [_sub_file(tmp_path, "Ep02.srt")])
        assert queue_cls.call_count == 1  # second click refused

    def test_curation_callback_gated_on_checkbox(self, tab, tmp_path):
        queue_cls = tab._queue_worker_cls
        tab.review_words_checkbox.setChecked(True)
        _mine(tab, [_sub_file(tmp_path)])
        assert queue_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge

    def test_start_resets_bar_and_swaps_buttons(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path)])
        assert tab.preview_button.isHidden()
        assert tab.mine_button.isHidden()
        assert not tab.cancel_button.isHidden()
        assert not tab.add_files_button.isEnabled()
        assert "Starting" in tab.overall_progress_widget.status_label.text()


class TestItemSlots:
    """Per-item slots are READ-ONLY on item state and compose the run bar."""

    def test_item_started_multi_file_status(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path, "Ep01.srt"), _sub_file(tmp_path, "Ep02.srt")])
        tab._on_item_started(0)
        assert "1/2" in tab.overall_progress_widget.status_label.text()
        assert "Ep01" in tab.overall_progress_widget.status_label.text()

    def test_item_started_single_file_plain_title(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path, "Ep01.srt")])
        tab._on_item_started(0)
        status = tab.overall_progress_widget.status_label.text()
        assert "Ep01" in status
        assert "1/1" not in status

    def test_item_progress_composes(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path, "Ep01.srt"), _sub_file(tmp_path, "Ep02.srt")])
        tab._on_item_started(1)
        tab._on_item_progress(1, "Definitions", 50)
        # (1 file done + 50%) / 2 files = 75%.
        assert tab.overall_progress_widget.progress_bar.value() == 75

    def test_item_progress_negative_pct_holds_bar(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path)])
        tab._on_item_started(0)
        tab._on_item_progress(0, "Working", 50)
        before = tab.overall_progress_widget.progress_bar.value()
        tab._on_item_progress(0, "Still working", -1)
        assert tab.overall_progress_widget.progress_bar.value() == before

    def test_item_finished_success_logs_and_forwards(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path)])
        result = MagicMock(cards_created=7)
        tab._on_item_finished(0, result, None, 1)
        assert "7 cards" in tab.log_widget.text_edit.toPlainText()
        tab._presenter.show_processing_result.assert_called_once_with(result)

    def test_item_finished_error_logged(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path)])
        tab._on_item_finished(0, None, "boom", 1)
        assert "boom" in tab.log_widget.text_edit.toPlainText()

    def test_item_finished_does_not_write_state(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path)])
        item = tab._run_items[0]
        status_before = item.status
        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)
        assert item.status is status_before  # slot never writes item state

    def test_out_of_range_idx_is_noop(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path)])
        tab._on_item_started(99)
        tab._on_item_finished(99, None, "x", 1)  # must not raise

    def test_queue_finished_logs_only_for_multi(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path)])
        tab._on_queue_finished()
        assert "Finished" not in tab.log_widget.text_edit.toPlainText()

        tab._on_worker_finished()  # release the run
        tab._on_clear_clicked()  # the list persists across runs — start fresh
        _mine(tab, [_sub_file(tmp_path, "a.srt"), _sub_file(tmp_path, "b.srt")])
        tab._on_queue_finished()
        assert "Finished 2 subtitle files" in tab.log_widget.text_edit.toPlainText()


class TestCleanup:
    """Cleanup restores buttons/bar on every run-exit path."""

    def test_cleanup_restores_buttons_and_bar(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path)])
        tab._on_worker_finished()
        assert not tab.preview_button.isHidden()
        assert not tab.mine_button.isHidden()
        assert tab.cancel_button.isHidden()
        assert tab.add_files_button.isEnabled()

    def test_cancel_disables_button_and_cancels_worker(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path)])
        worker = tab.worker_thread
        tab._on_cancel_clicked()
        worker.cancel.assert_called_once()
        assert not tab.cancel_button.isEnabled()


class TestCurationContext:
    """Subtitle curation stays table-only (base (None, None) context)."""

    def test_build_curation_context_is_table_only(self, tab, tmp_path):
        _mine(tab, [_sub_file(tmp_path)])
        assert tab._build_curation_context() == (None, None)

"""Tests for the novels sub-tab of the Reading tab.

``ReadingNovelsTab`` mines a single ``.epub``/``.txt`` book per run over the
shared ``_ReadingMiningTabBase`` lifecycle — there is NO queue: every run hands
the base exactly one ephemeral ``ReadingQueueItem``. Behaviour under test:

* Start: a valid book is classified by ``detect`` into one ephemeral item and
  launched via the base (1 item, curation gated by the checkbox,
  prebuilt-vs-factory processor path). Mine gives way to Cancel while a
  run is active.
* Invalid path (empty / wrong suffix / not a file) warns and starts no worker.
* Per-item signals are READ-ONLY on item state (the worker owns the lifecycle):
  they drive the single progress bar + log the outcome, never write status.
* Cleanup restores the Cancel button and the progress bar on every exit path.
* Drag-drop routes through the tab: the FileSelector and its inner QLineEdit
  both have drops disabled, so a drop landing on the input field is delivered to
  the tab handler (which fills the selector for a book, hints for manga).
* D8 (amended): ``_build_curation_context`` has no media context but wires the
  definition-pane ``lookup_fn`` from the worker's ``curation_processor`` (only
  the manga sub-tab overrides it further, for its page-image context).

Qt threads are never started — ``ReadingQueueWorker`` is class-level patched at
the base module so ``start()`` is a no-op and constructor kwargs can be
inspected.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDropEvent

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.models.reading import ReadingSourceRef

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_CREATE_TARGET = "anki_miner.gui.widgets._reading_mining_base.create_episode_processor"
# detect() now lives in the shared base helper (_detect_or_report), so patch it
# where the base module imports it.
_DETECT = "anki_miner.gui.widgets._reading_mining_base.detector.detect"
_URLS = "anki_miner.gui.widgets.reading_novels_tab.urls_from_event"


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig):
    """Instantiate a ReadingNovelsTab with the queue worker class patched.

    ``ReadingQueueWorker`` is patched at the base module where ``_launch_run``
    looks it up, so ``start()`` doesn't spawn a real QThread and constructor
    kwargs can be inspected via ``tab._queue_worker_cls``.
    """
    with patch(_WORKER_TARGET, autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")

        widget = ReadingNovelsTab(
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


def _make_ref(kind: str = "epub", title: str = "My Book") -> ReadingSourceRef:
    """Build a ReadingSourceRef for a single book."""
    ext = {"epub": ".epub", "txt": ".txt"}[kind]
    return ReadingSourceRef(
        kind=kind,  # type: ignore[arg-type]
        path=Path(f"/src/{title}{ext}"),
        image_root=None,
        title=title,
        volume=None,
    )


def _url(local_path: str):
    """A fake QUrl whose toLocalFile returns *local_path*."""
    u = MagicMock()
    u.toLocalFile.return_value = local_path
    return u


def _book_file(tmp_path: Path, name: str = "novel.epub") -> Path:
    """Create a real book file on disk so the file-mode selector validates."""
    book = tmp_path / name
    book.write_text("dummy", encoding="utf-8")
    return book


def _run(tab, book: Path, refs):
    """Select *book*, patch ``detect`` to return *refs*, click Mine."""
    tab.book_selector.set_path(str(book))
    with patch(_DETECT, return_value=list(refs)):
        tab._on_mine_clicked()


class TestInitialState:
    """Idle tab: Mine visible, Cancel hidden, no queue widgets."""

    def test_buttons_idle(self, tab):
        assert not tab.mine_button.isHidden()
        assert tab.cancel_button.isHidden()
        assert tab.worker_thread is None

    def test_review_checkbox_default_unchecked(self, tab):
        assert tab.review_words_checkbox.isChecked() is False

    def test_has_no_queue_widgets(self, tab):
        # Novels is single-file: none of the manga queue machinery exists.
        for attr in ("list_widget", "add_series_button", "add_volumes_button", "process_queue_button", "clear_button"):
            assert not hasattr(tab, attr)

    def test_section_header_says_novel(self, tab):
        from anki_miner.gui.widgets.enhanced import SectionHeader

        headers = tab.findChildren(SectionHeader)
        assert any(h.title_label.text() == "Novel" for h in headers)


class TestStartRun:
    """A valid book launches one ephemeral item through the base."""

    def test_mine_constructs_worker_one_item(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        _run(tab, _book_file(tmp_path), [_make_ref("epub", "Solo Book")])

        assert queue_cls.call_count == 1
        items = queue_cls.call_args.kwargs["items"]
        assert [i.title for i in items] == ["Solo Book"]
        assert len(items) == 1
        assert tab.worker_thread is not None
        tab.worker_thread.start.assert_called_once()

    def test_passes_prebuilt_processor_no_factory(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        _run(tab, _book_file(tmp_path), [_make_ref()])
        assert queue_cls.call_args.kwargs["processor"] is tab._processor
        assert queue_cls.call_args.kwargs["processor_factory"] is None

    def test_curation_callback_none_when_unchecked(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        _run(tab, _book_file(tmp_path), [_make_ref()])
        assert queue_cls.call_args.kwargs["curation_callback"] is None

    def test_curation_callback_bridge_when_checked(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        tab.review_words_checkbox.setChecked(True)
        _run(tab, _book_file(tmp_path), [_make_ref()])
        assert queue_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge

    def test_txt_book_accepted(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        _run(tab, _book_file(tmp_path, "book.txt"), [_make_ref("txt", "Plain")])
        assert queue_cls.call_count == 1

    def test_ephemeral_item_not_stored(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        # Single-file tab: the item lives only in the base run snapshot.
        assert len(tab._run_items) == 1
        assert not hasattr(tab, "_queue")

    def test_start_resets_bar_and_swaps_buttons(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        assert tab.mine_button.isHidden()
        assert not tab.cancel_button.isHidden()
        assert "Starting" in tab.progress_widget.status_label.text()

    def test_start_logs_run_banner(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        assert "1 items" in tab.log_widget.text_edit.toPlainText()

    def test_lazy_factory_when_no_processor(self, qtbot, test_config, tmp_path):
        """No cached processor → the base hands the worker a factory (off-thread)."""
        with (
            patch(_WORKER_TARGET, autospec=False) as q_cls,
            patch(_CREATE_TARGET) as mock_create,
        ):
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            built = MagicMock(name="LazyProcessor")
            mock_create.return_value = built

            widget = ReadingNovelsTab(config=test_config, processor=None, presenter=MagicMock(name="Presenter"))
            qtbot.addWidget(widget)
            try:
                _run(widget, _book_file(tmp_path), [_make_ref()])
                assert q_cls.call_args.kwargs["processor"] is None
                factory = q_cls.call_args.kwargs["processor_factory"]
                assert factory is not None
                assert mock_create.call_count == 0  # deferred to the worker thread
                assert factory() is built
            finally:
                widget.deleteLater()


class TestInvalidPath:
    """Invalid selections warn and never construct a worker."""

    def test_empty_path_warns_no_run(self, tab):
        queue_cls = tab._queue_worker_cls
        tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert tab.worker_thread is None
        assert "valid" in tab.log_widget.text_edit.toPlainText().lower()

    def test_nonexistent_file_warns_no_run(self, tab):
        queue_cls = tab._queue_worker_cls
        tab.book_selector.set_path("/no/such/book.epub")
        tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert "valid" in tab.log_widget.text_edit.toPlainText().lower()

    def test_wrong_suffix_warns_no_run(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        # A real file with an unsupported extension.
        bad = tmp_path / "manga.cbz"
        bad.write_text("x", encoding="utf-8")
        tab.book_selector.set_path(str(bad))
        with patch(_DETECT) as detect:
            tab._on_mine_clicked()
        detect.assert_not_called()  # rejected before detect
        assert queue_cls.call_count == 0

    def test_detect_error_surfaced_no_run(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        tab.book_selector.set_path(str(_book_file(tmp_path)))
        with patch(_DETECT, side_effect=SetupError("not a recognized reading source")):
            tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert "recognized reading source" in tab.log_widget.text_edit.toPlainText()

    def test_unexpected_detect_error_surfaced_no_run(self, tmp_path, tab):
        queue_cls = tab._queue_worker_cls
        tab.book_selector.set_path(str(_book_file(tmp_path)))
        with patch(_DETECT, side_effect=RuntimeError("boom")):
            tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert "boom" in tab.log_widget.text_edit.toPlainText()

    def test_run_refused_while_worker_active(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        queue_cls = tab._queue_worker_cls
        calls_before = queue_cls.call_count

        _run(tab, _book_file(tmp_path, "second.epub"), [_make_ref("epub", "Second")])
        assert queue_cls.call_count == calls_before  # no second worker


class TestPerItemSignalsReadOnly:
    """Per-item signals drive the bar/log but never write item state."""

    def test_item_started_sets_bar_no_status_write(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref("epub", "Solo Book")])
        item = tab._run_items[0]
        # Worker owns lifecycle: emulate it having already completed the item.
        from anki_miner.models.mining_queue import ReadyItemStatus

        item.status = ReadyItemStatus.COMPLETED
        item.cards_created = 9

        tab._on_item_started(0)

        assert item.status == ReadyItemStatus.COMPLETED
        assert item.cards_created == 9
        assert "Mining: Solo Book" in tab.progress_widget.status_label.text()
        assert tab.progress_widget.progress_bar.maximum() == 100

    def test_item_progress_determinate(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        tab._on_item_started(0)

        tab._on_item_progress(0, "Fetching definitions", 42)

        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.progress_bar.value() == 42
        assert "Fetching definitions" in tab.progress_widget.status_label.text()

    def test_item_progress_indeterminate(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        tab._on_item_started(0)

        tab._on_item_progress(0, "Fetching definitions", 42)
        tab._on_item_progress(0, "Parsing", -1)

        # pct < 0 holds the composed bar with a status update (no marquee).
        assert tab.progress_widget.progress_bar.maximum() == 100
        assert tab.progress_widget.progress_bar.value() == 42
        assert "Parsing" in tab.progress_widget.status_label.text()

    def test_item_finished_success_logs_and_forwards(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref("epub", "Solo Book")])
        result = MagicMock(cards_created=5)

        tab._on_item_finished(0, result, None, 1)

        assert "5 cards" in tab.log_widget.text_edit.toPlainText()
        tab._presenter.show_processing_result.assert_called_once_with(result)

    def test_item_finished_does_not_write_state(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        item = tab._run_items[0]
        before_status = item.status
        before_cards = item.cards_created

        tab._on_item_finished(0, MagicMock(cards_created=99), None, 1)

        assert item.status == before_status
        assert item.cards_created == before_cards

    def test_item_finished_error_logged(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref("epub", "Solo Book")])

        tab._on_item_finished(0, None, "SetupError: DRM", 1)

        assert "SetupError: DRM" in tab.log_widget.text_edit.toPlainText()
        tab._presenter.show_processing_result.assert_not_called()

    def test_item_finished_presenter_error_swallowed(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        tab._presenter.show_processing_result.side_effect = RuntimeError("presenter blew up")

        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)  # must not raise

    def test_item_started_out_of_range_idx_is_noop(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        status_before = tab.progress_widget.status_label.text()

        tab._on_item_started(99)

        assert tab.progress_widget.status_label.text() == status_before

    def test_item_finished_out_of_range_idx_is_noop(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])

        tab._on_item_finished(99, None, "err", 1)

        tab._presenter.show_processing_result.assert_not_called()

    def test_queue_finished_is_noop(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        worker = tab.worker_thread
        text_before = tab.log_widget.text_edit.toPlainText()

        tab._on_queue_finished()

        # Single item: no summary line, no state change.
        assert tab.log_widget.text_edit.toPlainText() == text_before
        assert tab.worker_thread is worker


class TestAfterRunCleanup:
    """The base cleanup slot restores the Cancel button and the bar."""

    def test_cleanup_restores_buttons_and_bar(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        tab._on_item_started(0)
        tab._on_item_progress(0, "Parsing", -1)
        tab._on_cancel_clicked()
        assert tab.cancel_button.text() == "Cancelling…"

        tab._on_worker_finished()

        assert tab.cancel_button.text() == "Cancel"
        assert tab.cancel_button.isEnabled()
        assert tab.progress_widget.progress_bar.maximum() == 100  # busy indicator reset
        assert tab.progress_widget.status_label.text() == "Cancelled"
        assert tab.worker_thread is None
        assert tab._run_items == []
        # Idle again: Mine restored, Cancel hidden.
        assert tab.cancel_button.isHidden()


class TestCancel:
    """Cancel forwards to worker.cancel() and releases any curation dialog."""

    def test_cancel_forwards_and_relabels(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        worker = tab.worker_thread

        tab._on_cancel_clicked()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        assert not tab.cancel_button.isEnabled()
        assert tab.cancel_button.text() == "Cancelling…"

    def test_cancel_releases_active_curation_dialog(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        with patch.object(tab, "_cancel_active_curation_dialog") as cancel:
            tab._on_cancel_clicked()
            cancel.assert_called_once()

    def test_cancel_noop_when_no_worker(self, tab):
        tab._on_cancel_clicked()  # must not raise


def _resolve_drop_target(widget):
    """Mimic Qt's DnD target selection: the nearest ``acceptDrops()`` ancestor.

    Qt's drag manager delivers a drop to the deepest widget under the cursor
    whose ``acceptDrops()`` is True, skipping non-accepting descendants — so a
    drop physically over the input field is routed here exactly as it is at
    runtime, driven only by the ``setAcceptDrops`` flags the tab configures.
    """
    w = widget
    while w is not None and not w.acceptDrops():
        w = w.parentWidget()
    return w


class TestDragDrop:
    """Drops route through the tab; the selector + its input never consume them."""

    def test_selector_and_input_reject_drops(self, tab):
        # Both disabled so the drag manager falls through to the tab.
        assert tab.book_selector.acceptDrops() is False
        assert tab.book_selector.input.acceptDrops() is False
        assert tab.acceptDrops() is True

    def test_drop_on_input_field_routes_to_tab(self, tmp_path, tab):
        """A drop landing on the INPUT FIELD is delivered to the tab handler.

        Resolves the drop target the way Qt does (nearest acceptDrops ancestor
        of the input) and delivers a REAL ``QDropEvent`` there — never calling
        ``tab.dropEvent`` directly. The target being the tab (not the input or
        the selector) is what proves both ``setAcceptDrops(False)`` calls route
        the drop through the tab.
        """
        book = _book_file(tmp_path)
        target = _resolve_drop_target(tab.book_selector.input)
        assert target is tab  # skipped the input AND the FileSelector

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(book))])
        event = QDropEvent(
            QPointF(1.0, 1.0),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        target.dropEvent(event)

        assert tab.book_selector.get_path() == str(book)
        assert event.isAccepted()

    def test_drop_book_fills_selector(self, tab):
        event = MagicMock()
        with patch(_URLS, return_value=[_url("/src/novel.epub")]):
            tab.dropEvent(event)
        assert tab.book_selector.get_path() == "/src/novel.epub"
        event.acceptProposedAction.assert_called_once()

    def test_drop_first_book_wins(self, tab):
        event = MagicMock()
        with patch(_URLS, return_value=[_url("/src/a.epub"), _url("/src/b.txt")]):
            tab.dropEvent(event)
        assert tab.book_selector.get_path() == "/src/a.epub"

    def test_drop_manga_file_hints_no_path(self, tab):
        event = MagicMock()
        with patch(_URLS, return_value=[_url("/src/vol.mokuro")]):
            tab.dropEvent(event)
        assert tab.book_selector.get_path() == ""
        assert "manga" in tab.log_widget.text_edit.toPlainText().lower()
        event.acceptProposedAction.assert_called_once()

    def test_drop_folder_hints_no_path(self, tmp_path, tab):
        event = MagicMock()
        with patch(_URLS, return_value=[_url(str(tmp_path))]):
            tab.dropEvent(event)
        assert tab.book_selector.get_path() == ""
        assert "manga" in tab.log_widget.text_edit.toPlainText().lower()

    def test_drop_subtitle_file_hints_no_path(self, tab):
        event = MagicMock()
        with patch(_URLS, return_value=[_url("/src/ep01.srt")]):
            tab.dropEvent(event)
        assert tab.book_selector.get_path() == ""
        assert "Subtitles tab" in tab.log_widget.text_edit.toPlainText()
        event.acceptProposedAction.assert_called_once()

    def test_drop_none_event_is_noop(self, tab):
        tab.dropEvent(None)  # must not raise
        assert tab.book_selector.get_path() == ""

    def test_drag_enter_accepts_book_manga_and_subtitle(self, tab):
        for name in ("/src/a.epub", "/src/a.txt", "/src/a.mokuro", "/src/a.cbz", "/src/a.srt"):
            event = MagicMock()
            with patch(_URLS, return_value=[_url(name)]):
                tab.dragEnterEvent(event)
            event.acceptProposedAction.assert_called_once()

    def test_drag_enter_none_event_is_noop(self, tab):
        tab.dragEnterEvent(None)  # must not raise


class TestCurationContext:
    """Novels curation has no media context but wires the definition pane."""

    def test_build_curation_context_is_none_none_without_worker(self, tab):
        # No worker → no processor → no lookup_fn either.
        assert tab._build_curation_context() == (None, None)

    def test_build_curation_context_wires_lookup_fn_with_worker(self, tmp_path, tab):
        # With a live worker, no media context is sourced (novels have none),
        # but the definition-pane lookup_fn comes from the worker's processor.
        _run(tab, _book_file(tmp_path), [_make_ref()])
        ctx, lookup_fn = tab._build_curation_context()
        assert ctx is None
        assert lookup_fn is tab.worker_thread.curation_processor.offline_lookup_fn


class TestShutdownRelease:
    """shutdown/release smoke — inherited from the base, exercised on this tab."""

    def test_shutdown_cancels_worker(self, tmp_path, tab):
        _run(tab, _book_file(tmp_path), [_make_ref()])
        worker = tab.worker_thread

        tab.shutdown()

        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        assert tab.worker_thread is None

    def test_shutdown_with_nothing_active(self, tab):
        tab.shutdown()  # must not raise

    def test_release_dictionary_resources_when_idle(self, tab):
        processor = tab._processor
        assert tab.release_dictionary_resources() is True
        processor.release_dictionary_resources.assert_called_once()
        assert tab._processor is None

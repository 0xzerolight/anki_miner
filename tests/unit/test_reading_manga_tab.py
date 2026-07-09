"""Tests for the manga sub-tab of the Reading tab.

``ReadingMangaTab`` is a single auto-detecting folder card (no queue): pick a
folder, then Preview or Mine. The folder is classified by ``detect`` on click.
Behaviour under test:

* Preview: classify the folder and open a structural
  ``MangaVolumesPreviewDialog`` listing the detected volume(s). No worker, no
  tokenizing, no cards.
* Mine: classify the folder into one ephemeral ``ReadingQueueItem`` per volume
  and launch them through the base (a single volume hides the overall bar; a
  series of >1 shows it).
* Empty path warns; a ``detect`` ``SetupError`` is surfaced verbatim; neither
  starts a run or opens the dialog.
* Buttons: pure derived state — Preview/Mine give way to Cancel during a run.
* Per-item signals are READ-ONLY on item state (the worker owns the lifecycle):
  they drive the two progress bars + log the outcome, never write status/cards.
* Drag-drop routes through the tab: the FileSelector and its inner QLineEdit
  both have drops disabled, so a drop lands on the tab (folder/manga file fills
  the selector; a dropped novel earns a cross-tab hint).
* D8 (amended): ``_build_curation_context`` builds a page-image context from
  the worker's published manga ``curation_document``; it falls back to the
  base ``(None, None)`` for no worker / no document / book-kind documents /
  image-less volumes. Novels curation stays table-only.

Qt threads are never started — ``ReadingQueueWorker`` is class-level patched at
the base module so ``start()`` is a no-op and constructor kwargs can be
inspected. The preview dialog is patched so no modal opens.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDropEvent

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.models.reading_queue import ReadingItemStatus
from anki_miner.services.reading.models import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_CREATE_TARGET = "anki_miner.gui.widgets._reading_mining_base.create_episode_processor"
# detect() runs in the shared base helper (_detect_or_report); patch it there.
_DETECT = "anki_miner.gui.widgets._reading_mining_base.detector.detect"
_DIALOG = "anki_miner.gui.widgets.reading_manga_tab.MangaVolumesPreviewDialog"
_URLS = "anki_miner.gui.widgets.reading_manga_tab.urls_from_event"


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig):
    """Instantiate a ReadingMangaTab with the queue worker class patched.

    ``ReadingQueueWorker`` is patched at the base module where ``_launch_run``
    looks it up, so ``start()`` doesn't spawn a real QThread and constructor
    kwargs can be inspected via ``tab._queue_worker_cls``.
    """
    with patch(_WORKER_TARGET, autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")

        widget = ReadingMangaTab(
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


def _make_ref(kind: str = "mokuro", title: str = "Series Vol.1") -> ReadingSourceRef:
    """Build a ReadingSourceRef for a single volume/book."""
    ext = {"mokuro": ".mokuro", "epub": ".epub", "txt": ".txt"}[kind]
    return ReadingSourceRef(
        kind=kind,  # type: ignore[arg-type]
        path=Path(f"/src/{title}{ext}"),
        image_root=None,
        title=title,
        volume="1" if kind == "mokuro" else None,
    )


def _series(n: int) -> list[ReadingSourceRef]:
    """Build *n* volume refs for one series."""
    return [_make_ref("mokuro", f"Series Vol.{i + 1}") for i in range(n)]


def _url(local_path: str):
    """A fake QUrl whose toLocalFile returns *local_path*."""
    u = MagicMock()
    u.toLocalFile.return_value = local_path
    return u


def _mine(tab, refs, folder: str = "/src/series"):
    """Select *folder*, patch ``detect`` to return *refs*, click Mine."""
    tab.volume_folder_selector.set_path(folder)
    with patch(_DETECT, return_value=list(refs)):
        tab._on_mine_clicked()


def _preview(tab, refs, folder: str = "/src/series"):
    """Select *folder*, patch ``detect`` + the dialog, click Preview.

    Returns the patched dialog class mock so tests can inspect construction.
    """
    tab.volume_folder_selector.set_path(folder)
    with patch(_DETECT, return_value=list(refs)), patch(_DIALOG) as dialog_cls:
        tab._on_preview_clicked()
    return dialog_cls


class TestInitialState:
    """Idle tab: Preview/Mine visible, Cancel hidden, no queue widgets."""

    def test_buttons_idle(self, tab):
        assert not tab.preview_button.isHidden()
        assert not tab.mine_button.isHidden()
        assert tab.cancel_button.isHidden()
        assert tab.worker_thread is None

    def test_review_checkbox_default_unchecked(self, tab):
        assert tab.review_words_checkbox.isChecked() is False

    def test_has_no_queue_widgets(self, tab):
        # The queue was removed: none of its machinery exists.
        for attr in (
            "list_widget",
            "add_series_button",
            "add_volumes_button",
            "process_queue_button",
            "clear_button",
            "_queue",
        ):
            assert not hasattr(tab, attr)

    def test_overall_bar_hidden_initially(self, tab):
        assert tab.overall_progress_widget.isHidden()
        assert tab.overall_header.isHidden()

    def test_section_header_says_manga(self, tab):
        from anki_miner.gui.widgets.enhanced import SectionHeader

        headers = tab.findChildren(SectionHeader)
        assert any(h.title_label.text() == "Manga" for h in headers)


class TestPreview:
    """Preview classifies the folder and opens the structural dialog. No worker."""

    def test_preview_opens_dialog_with_refs(self, tab):
        refs = _series(3)
        dialog_cls = _preview(tab, refs)
        dialog_cls.assert_called_once()
        # First positional arg is the detected refs list.
        assert dialog_cls.call_args.args[0] == refs
        dialog_cls.return_value.exec.assert_called_once()

    def test_preview_single_volume_opens_dialog(self, tab):
        dialog_cls = _preview(tab, [_make_ref()])
        dialog_cls.assert_called_once()

    def test_preview_starts_no_worker(self, tab):
        queue_cls = tab._queue_worker_cls
        _preview(tab, _series(2))
        assert queue_cls.call_count == 0
        assert tab.worker_thread is None

    def test_preview_empty_path_warns_no_dialog(self, tab):
        with patch(_DIALOG) as dialog_cls:
            tab._on_preview_clicked()
        dialog_cls.assert_not_called()
        assert "folder" in tab.log_widget.text_edit.toPlainText().lower()

    def test_preview_detect_error_surfaced_no_dialog(self, tab):
        tab.volume_folder_selector.set_path("/src/bad")
        with (
            patch(_DETECT, side_effect=SetupError("not a recognized reading source")),
            patch(_DIALOG) as dialog_cls,
        ):
            tab._on_preview_clicked()
        dialog_cls.assert_not_called()
        assert "recognized reading source" in tab.log_widget.text_edit.toPlainText()

    def test_preview_refused_while_worker_active(self, tab):
        _mine(tab, [_make_ref()])
        with patch(_DIALOG) as dialog_cls:
            tab._on_preview_clicked()
        dialog_cls.assert_not_called()


class TestMineSingleVolume:
    """A single-volume folder mines one ephemeral item; overall bar hidden."""

    def test_mine_constructs_worker_one_item(self, tab):
        queue_cls = tab._queue_worker_cls
        _mine(tab, [_make_ref("mokuro", "Solo Vol")], folder="/src/vol")
        assert queue_cls.call_count == 1
        items = queue_cls.call_args.kwargs["items"]
        assert [i.title for i in items] == ["Solo Vol"]
        assert tab.worker_thread is not None
        tab.worker_thread.start.assert_called_once()

    def test_mine_preview_flag_false(self, tab):
        queue_cls = tab._queue_worker_cls
        _mine(tab, [_make_ref()])
        assert queue_cls.call_args.kwargs["preview_mode"] is False

    def test_mine_single_hides_overall_bar(self, tab):
        _mine(tab, [_make_ref()])
        assert tab.overall_progress_widget.isHidden()
        assert tab.overall_header.isHidden()
        assert "Starting" in tab.current_progress_widget.status_label.text()

    def test_passes_prebuilt_processor_no_factory(self, tab):
        queue_cls = tab._queue_worker_cls
        _mine(tab, [_make_ref()])
        assert queue_cls.call_args.kwargs["processor"] is tab._processor
        assert queue_cls.call_args.kwargs["processor_factory"] is None

    def test_curation_callback_none_when_unchecked(self, tab):
        queue_cls = tab._queue_worker_cls
        _mine(tab, [_make_ref()])
        assert queue_cls.call_args.kwargs["curation_callback"] is None

    def test_curation_callback_bridge_when_checked(self, tab):
        queue_cls = tab._queue_worker_cls
        tab.review_words_checkbox.setChecked(True)
        _mine(tab, [_make_ref()])
        assert queue_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge

    def test_ephemeral_items_not_stored(self, tab):
        _mine(tab, [_make_ref()])
        # No queue: items live only in the base run snapshot.
        assert len(tab._run_items) == 1
        assert not hasattr(tab, "_queue")

    def test_start_swaps_buttons(self, tab):
        _mine(tab, [_make_ref()])
        assert tab.preview_button.isHidden()
        assert tab.mine_button.isHidden()
        assert not tab.cancel_button.isHidden()


class TestMineSeries:
    """A series folder mines every volume; overall bar shown + advanced."""

    def test_mine_constructs_worker_n_items(self, tab):
        queue_cls = tab._queue_worker_cls
        refs = _series(4)
        _mine(tab, refs)
        items = queue_cls.call_args.kwargs["items"]
        assert [i.title for i in items] == [r.title for r in refs]
        assert len(items) == 4

    def test_mine_series_shows_overall_bar(self, tab):
        _mine(tab, _series(3))
        assert not tab.overall_progress_widget.isHidden()
        assert not tab.overall_header.isHidden()

    def test_item_finished_advances_overall_bar(self, tab):
        _mine(tab, _series(2))
        # Worker owns lifecycle: emulate it having completed item 0.
        tab._run_items[0].status = ReadingItemStatus.COMPLETED
        tab._on_item_finished(0, MagicMock(cards_created=3), None, 1)
        # set_progress renders a percentage: 1 of 2 volumes done → 50%.
        assert tab.overall_progress_widget.progress_bar.value() == 50
        assert "1/2" in tab.overall_progress_widget.status_label.text()

    def test_item_started_series_label(self, tab):
        _mine(tab, _series(3))
        tab._on_item_started(1)
        assert "Mining 2 of 3" in tab.current_progress_widget.status_label.text()

    def test_queue_finished_summary_for_series(self, tab):
        _mine(tab, _series(2))
        tab._run_items[0].status = ReadingItemStatus.COMPLETED
        tab._run_items[1].status = ReadingItemStatus.ERROR
        tab._on_queue_finished()
        text = tab.log_widget.text_edit.toPlainText()
        assert "1 succeeded" in text
        assert "1 failed" in text

    def test_lazy_factory_when_no_processor(self, qtbot, test_config):
        """No cached processor → the base hands the worker a factory (off-thread)."""
        with (
            patch(_WORKER_TARGET, autospec=False) as q_cls,
            patch(_CREATE_TARGET) as mock_create,
        ):
            q_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            built = MagicMock(name="LazyProcessor")
            mock_create.return_value = built

            widget = ReadingMangaTab(config=test_config, processor=None, presenter=MagicMock(name="Presenter"))
            qtbot.addWidget(widget)
            try:
                _mine(widget, [_make_ref()])
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
        assert "folder" in tab.log_widget.text_edit.toPlainText().lower()

    def test_detect_error_surfaced_no_run(self, tab):
        queue_cls = tab._queue_worker_cls
        tab.volume_folder_selector.set_path("/src/bad")
        with patch(_DETECT, side_effect=SetupError("no .mokuro volumes inside it")):
            tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert "no .mokuro volumes" in tab.log_widget.text_edit.toPlainText()

    def test_unexpected_detect_error_surfaced_no_run(self, tab):
        queue_cls = tab._queue_worker_cls
        tab.volume_folder_selector.set_path("/src/bad")
        with patch(_DETECT, side_effect=RuntimeError("boom")):
            tab._on_mine_clicked()
        assert queue_cls.call_count == 0
        assert "boom" in tab.log_widget.text_edit.toPlainText()

    def test_run_refused_while_worker_active(self, tab):
        _mine(tab, [_make_ref()])
        queue_cls = tab._queue_worker_cls
        calls_before = queue_cls.call_count
        _mine(tab, [_make_ref("mokuro", "Second")], folder="/src/second")
        assert queue_cls.call_count == calls_before  # no second worker


class TestPerItemSignalsReadOnly:
    """Per-item signals drive the bars/log but never write item state."""

    def test_item_started_sets_bar_no_status_write(self, tab):
        _mine(tab, [_make_ref("mokuro", "Solo Vol")])
        item = tab._run_items[0]
        item.status = ReadingItemStatus.COMPLETED
        item.cards_created = 9

        tab._on_item_started(0)

        assert item.status == ReadingItemStatus.COMPLETED
        assert item.cards_created == 9
        assert "Mining: Solo Vol" in tab.current_progress_widget.status_label.text()
        assert tab.current_progress_widget.progress_bar.maximum() == 100

    def test_item_progress_determinate(self, tab):
        _mine(tab, [_make_ref()])
        tab._on_item_started(0)
        tab._on_item_progress(0, "Fetching definitions", 42)
        assert tab.current_progress_widget.progress_bar.value() == 42
        assert "Fetching definitions" in tab.current_progress_widget.status_label.text()

    def test_item_progress_indeterminate(self, tab):
        _mine(tab, [_make_ref()])
        tab._on_item_started(0)
        tab._on_item_progress(0, "Parsing", -1)
        assert tab.current_progress_widget.progress_bar.maximum() == 0  # busy indicator
        assert "Parsing" in tab.current_progress_widget.status_label.text()

    def test_item_finished_success_logs_and_forwards(self, tab):
        _mine(tab, [_make_ref("mokuro", "Solo Vol")])
        result = MagicMock(cards_created=5)
        tab._on_item_finished(0, result, None, 1)
        assert "5 cards" in tab.log_widget.text_edit.toPlainText()
        tab._presenter.show_processing_result.assert_called_once_with(result)

    def test_item_finished_does_not_write_state(self, tab):
        _mine(tab, [_make_ref()])
        item = tab._run_items[0]
        before_status = item.status
        before_cards = item.cards_created
        tab._on_item_finished(0, MagicMock(cards_created=99), None, 1)
        assert item.status == before_status
        assert item.cards_created == before_cards

    def test_item_finished_error_logged(self, tab):
        _mine(tab, [_make_ref("mokuro", "Solo Vol")])
        tab._on_item_finished(0, None, "SetupError: DRM", 1)
        assert "SetupError: DRM" in tab.log_widget.text_edit.toPlainText()
        tab._presenter.show_processing_result.assert_not_called()

    def test_item_finished_presenter_error_swallowed(self, tab):
        _mine(tab, [_make_ref()])
        tab._presenter.show_processing_result.side_effect = RuntimeError("presenter blew up")
        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)  # must not raise

    def test_item_started_out_of_range_idx_is_noop(self, tab):
        _mine(tab, [_make_ref()])
        status_before = tab.current_progress_widget.status_label.text()
        tab._on_item_started(99)
        assert tab.current_progress_widget.status_label.text() == status_before

    def test_single_volume_queue_finished_is_noop(self, tab):
        _mine(tab, [_make_ref()])
        text_before = tab.log_widget.text_edit.toPlainText()
        tab._on_item_finished(0, MagicMock(cards_created=1), None, 1)
        text_after_finish = tab.log_widget.text_edit.toPlainText()
        tab._on_queue_finished()
        # Single item: queue_finished adds no summary beyond the per-item line.
        assert tab.log_widget.text_edit.toPlainText() == text_after_finish
        assert text_after_finish != text_before


class TestAfterRunCleanup:
    """The base cleanup slot restores the Cancel button and both bars."""

    def test_cleanup_restores_buttons_and_bars(self, tab):
        _mine(tab, _series(2))
        tab._on_item_started(0)
        tab._on_cancel_clicked()
        assert tab.cancel_button.text() == "Cancelling…"

        tab._on_worker_finished()

        assert tab.cancel_button.text() == "Cancel"
        assert tab.cancel_button.isEnabled()
        assert tab.overall_progress_widget.isHidden()  # overall re-hidden
        assert tab.current_progress_widget.status_label.text() == "Ready"
        assert tab.worker_thread is None
        assert tab._run_items == []
        assert not tab.preview_button.isHidden()
        assert tab.cancel_button.isHidden()


class TestCancel:
    """Cancel forwards to worker.cancel() and releases any curation dialog."""

    def test_cancel_forwards_and_relabels(self, tab):
        _mine(tab, [_make_ref()])
        worker = tab.worker_thread
        tab._on_cancel_clicked()
        worker.cancel.assert_called_once()  # type: ignore[union-attr]
        assert not tab.cancel_button.isEnabled()
        assert tab.cancel_button.text() == "Cancelling…"

    def test_cancel_releases_active_curation_dialog(self, tab):
        _mine(tab, [_make_ref()])
        with patch.object(tab, "_cancel_active_curation_dialog") as cancel:
            tab._on_cancel_clicked()
            cancel.assert_called_once()

    def test_cancel_noop_when_no_worker(self, tab):
        tab._on_cancel_clicked()  # must not raise


def _resolve_drop_target(widget):
    """Mimic Qt's DnD target selection: the nearest ``acceptDrops()`` ancestor."""
    w = widget
    while w is not None and not w.acceptDrops():
        w = w.parentWidget()
    return w


class TestDragDrop:
    """Drops route through the tab; the selector + its input never consume them."""

    def test_selector_and_input_reject_drops(self, tab):
        assert tab.volume_folder_selector.acceptDrops() is False
        assert tab.volume_folder_selector.input.acceptDrops() is False
        assert tab.acceptDrops() is True

    def test_drop_on_input_field_routes_to_tab(self, tmp_path, tab):
        """A drop landing on the INPUT FIELD is delivered to the tab handler."""
        folder = tmp_path / "series"
        folder.mkdir()
        target = _resolve_drop_target(tab.volume_folder_selector.input)
        assert target is tab  # skipped the input AND the FileSelector

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(folder))])
        event = QDropEvent(
            QPointF(1.0, 1.0),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        target.dropEvent(event)

        assert tab.volume_folder_selector.get_path() == str(folder)
        assert event.isAccepted()

    def test_drop_folder_fills_selector(self, tmp_path, tab):
        folder = tmp_path / "series"
        folder.mkdir()
        event = MagicMock()
        with patch(_URLS, return_value=[_url(str(folder))]):
            tab.dropEvent(event)
        assert tab.volume_folder_selector.get_path() == str(folder)
        event.acceptProposedAction.assert_called_once()

    def test_drop_manga_file_fills_selector(self, tab):
        event = MagicMock()
        with patch(_URLS, return_value=[_url("/src/vol.cbz")]):
            tab.dropEvent(event)
        assert tab.volume_folder_selector.get_path() == "/src/vol.cbz"

    def test_drop_first_source_wins(self, tmp_path, tab):
        folder = tmp_path / "series"
        folder.mkdir()
        event = MagicMock()
        with patch(_URLS, return_value=[_url(str(folder)), _url("/src/other.cbz")]):
            tab.dropEvent(event)
        assert tab.volume_folder_selector.get_path() == str(folder)

    def test_drop_novel_hints_no_path(self, tab):
        event = MagicMock()
        with patch(_URLS, return_value=[_url("/src/book.epub")]):
            tab.dropEvent(event)
        assert tab.volume_folder_selector.get_path() == ""
        assert "novels" in tab.log_widget.text_edit.toPlainText().lower()
        event.acceptProposedAction.assert_called_once()

    def test_drop_none_event_is_noop(self, tab):
        tab.dropEvent(None)  # must not raise
        assert tab.volume_folder_selector.get_path() == ""

    def test_drag_enter_accepts_folder_manga_and_novel(self, tmp_path, tab):
        folder = tmp_path / "d"
        folder.mkdir()
        for name in (str(folder), "/src/a.mokuro", "/src/a.cbz", "/src/a.epub"):
            event = MagicMock()
            with patch(_URLS, return_value=[_url(name)]):
                tab.dragEnterEvent(event)
            event.acceptProposedAction.assert_called_once()

    def test_drag_enter_none_event_is_noop(self, tab):
        tab.dragEnterEvent(None)  # must not raise


def _manga_document(*, kind: str = "manga", with_images: bool = True) -> ReadingDocument:
    """A two-unit manga document (units share one page image)."""
    ref = ImageRef(Path("/pages/001.png")) if with_images else None
    doc = ReadingDocument(title="Series", kind=kind, series="Series", episode="1")  # type: ignore[arg-type]
    doc.units = [
        ReadingUnit(text="ふきだし", index=0, location_label="p.1", image_ref=ref, block_box=(1, 2, 3, 4)),
        ReadingUnit(text="つづき", index=1, location_label="p.1", image_ref=ref),
    ]
    return doc


class TestCurationContext:
    """D8 (amended): manga builds a page-image context off the parked worker.

    Falls back to the base (None, None) when there is no worker, no published
    document, a book-kind document, or a volume with no page images.
    """

    def test_build_curation_context_is_none_none_without_worker(self, tab):
        assert tab._build_curation_context() == (None, None)

    def test_manga_document_yields_page_units_context(self, tab):
        _mine(tab, [_make_ref()])
        doc = _manga_document()
        tab.worker_thread.curation_document = doc

        ctx, lookup_fn = tab._build_curation_context()

        assert lookup_fn is None
        assert ctx is not None
        assert ctx.video_file is None
        # Every unit is mapped by index — including imageless ones, so
        # unmatched pages still show their page label in the placeholder.
        assert ctx.page_units == {0: doc.units[0], 1: doc.units[1]}

    def test_none_document_falls_back(self, tab):
        _mine(tab, [_make_ref()])
        tab.worker_thread.curation_document = None
        assert tab._build_curation_context() == (None, None)

    def test_book_kind_falls_back(self, tab):
        _mine(tab, [_make_ref()])
        tab.worker_thread.curation_document = _manga_document(kind="book")
        assert tab._build_curation_context() == (None, None)

    def test_imageless_volume_falls_back(self, tab):
        _mine(tab, [_make_ref()])
        tab.worker_thread.curation_document = _manga_document(with_images=False)
        assert tab._build_curation_context() == (None, None)


class TestShutdownRelease:
    """shutdown/release smoke — inherited from the base, exercised on this tab."""

    def test_shutdown_cancels_worker(self, tab):
        _mine(tab, [_make_ref()])
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

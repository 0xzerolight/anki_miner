"""Tests for the manga sub-tab of the Reading tab.

``ReadingMangaTab`` is a single auto-detecting folder card (no queue): pick a
folder, then Mine. The folder is classified by ``detect`` on click.
Behaviour under test:

* Mine: classify the folder into one ephemeral ``ReadingQueueItem`` per volume
  and launch them through the base (a single volume hides the overall bar; a
  series of >1 shows it).
* Empty path warns; a ``detect`` ``SetupError`` is surfaced verbatim; neither
  starts a run or opens the dialog.
* Buttons: pure derived state — Mine gives way to Cancel during a run.
* Per-item signals are READ-ONLY on item state (the worker owns the lifecycle):
  they drive the two progress bars + log the outcome, never write status/cards.
* Drag-drop routes through the tab: the FileSelector and its inner QLineEdit
  both have drops disabled, so a drop lands on the tab (folder/manga file fills
  the selector; a dropped novel earns a cross-tab hint).
* D8 (amended): ``_build_curation_context`` builds a page-image context from
  the worker's published manga ``curation_document`` and wires the definition-
  pane ``lookup_fn`` from ``curation_processor``; the media context falls back
  to ``None`` for no document / book-kind documents / image-less volumes (with
  no worker at all, both are ``None``).

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
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.models.reading import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)
from anki_miner.models.reading_queue import ReadingItemStatus

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_CREATE_TARGET = "anki_miner.gui.widgets._reading_mining_base.create_episode_processor"
# detect() runs in the shared base helper (_detect_or_report); patch it there.
_DETECT = "anki_miner.gui.widgets._reading_mining_base.detector.detect"
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


class TestInitialState:
    """Idle tab: Mine visible, Cancel hidden, no queue widgets."""

    def test_buttons_idle(self, tab):
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

    def test_single_bar_visible_initially(self, tab):
        # One whole-run bar, always present (no hidden per-volume/overall split).
        assert not hasattr(tab, "current_progress_widget")
        assert not hasattr(tab, "overall_header")

    def test_section_header_says_manga(self, tab):
        from anki_miner.gui.widgets.enhanced import SectionHeader

        headers = tab.findChildren(SectionHeader)
        assert any(h.title_label.text() == "Manga" for h in headers)


class TestMineSingleVolume:
    """A single-volume folder mines one ephemeral item; overall bar hidden."""

    def test_mine_constructs_worker_one_item(self, tab):
        queue_cls = tab._queue_worker_cls
        _mine(tab, [_make_ref("mokuro", "Solo Vol")], folder="/src/vol")
        assert queue_cls.call_count == 1
        items = queue_cls.call_args.kwargs["items"]
        # mokuro item title carries the volume (Y6): "<series> — <volume>".
        assert [i.title for i in items] == ["Solo Vol — 1"]
        assert tab.worker_thread is not None
        tab.worker_thread.start.assert_called_once()

    def test_mokuro_item_title_includes_volume(self, tab):
        # Y6: for a mokuro ref, title is the SERIES and volume the actual
        # volume — the queue item must name the volume so per-volume progress/
        # log lines are distinguishable.
        queue_cls = tab._queue_worker_cls
        ref = ReadingSourceRef(
            kind="mokuro",
            path=Path("/src/MySeries/Vol.3.mokuro"),
            image_root=None,
            title="MySeries",
            volume="Vol.3",
        )
        _mine(tab, [ref], folder="/src/MySeries")
        items = queue_cls.call_args.kwargs["items"]
        assert items[0].title == "MySeries — Vol.3"

    def test_mokuro_item_title_bare_without_volume(self, tab):
        # A mokuro ref lacking a volume stays labelled by its title alone.
        queue_cls = tab._queue_worker_cls
        ref = ReadingSourceRef(
            kind="mokuro",
            path=Path("/src/OneShot.mokuro"),
            image_root=None,
            title="OneShot",
            volume=None,
        )
        _mine(tab, [ref], folder="/src/one")
        items = queue_cls.call_args.kwargs["items"]
        assert items[0].title == "OneShot"

    def test_mine_single_uses_single_bar(self, tab):
        _mine(tab, [_make_ref()])
        assert "Starting" in tab.overall_progress_widget.status_label.text()
        assert tab.overall_progress_widget.progress_bar.value() == 0

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
        assert tab.mine_button.isHidden()
        assert not tab.cancel_button.isHidden()


class TestMineSeries:
    """A series folder mines every volume; overall bar shown + advanced."""

    def test_mine_constructs_worker_n_items(self, tab):
        queue_cls = tab._queue_worker_cls
        refs = _series(4)
        _mine(tab, refs)
        items = queue_cls.call_args.kwargs["items"]
        # Each mokuro volume's item title carries its volume (Y6).
        assert [i.title for i in items] == [f"{r.title} — {r.volume}" for r in refs]
        assert len(items) == 4

    def test_mine_series_seeds_composition(self, tab):
        _mine(tab, _series(3))
        assert tab._items_total == 3

    def test_item_finished_advances_composed_bar(self, tab):
        _mine(tab, _series(2))
        # Worker owns lifecycle: emulate it having completed item 0.
        tab._run_items[0].status = ReadingItemStatus.COMPLETED
        tab._on_item_finished(0, MagicMock(cards_created=3, new_words_found=3), None, 1)
        # Composed: 1 of 2 volumes done -> 50%.
        assert tab.overall_progress_widget.progress_bar.value() == 50

    def test_item_started_series_label(self, tab):
        _mine(tab, _series(3))
        tab._on_item_started(1)
        assert "Volume 2/3" in tab.overall_progress_widget.status_label.text()

    def test_item_progress_composes_across_volumes(self, tab):
        """Volume 2's own 50% renders as (1 + 0.5)/3 = 50% of the whole run —
        the bar never resets at a volume boundary."""
        _mine(tab, _series(3))
        tab._on_item_started(1)
        tab._on_item_progress(1, "Fetching definitions", 50)
        assert tab.overall_progress_widget.progress_bar.value() == 50
        text = tab.overall_progress_widget.status_label.text()
        assert "Volume 2/3" in text and "Fetching definitions" in text

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
        assert "Solo Vol" in tab.overall_progress_widget.status_label.text()
        assert tab.overall_progress_widget.progress_bar.maximum() == 100

    def test_item_progress_determinate(self, tab):
        _mine(tab, [_make_ref()])
        tab._on_item_started(0)
        tab._on_item_progress(0, "Fetching definitions", 42)
        # Single item: composed value equals the item's own percent.
        assert tab.overall_progress_widget.progress_bar.value() == 42
        assert "Fetching definitions" in tab.overall_progress_widget.status_label.text()

    def test_item_progress_indeterminate_holds_bar(self, tab):
        """pct < 0 holds the composed bar (status update only) — a mid-run
        marquee would read as a reset."""
        _mine(tab, [_make_ref()])
        tab._on_item_started(0)
        tab._on_item_progress(0, "Fetching definitions", 42)
        tab._on_item_progress(0, "Parsing", -1)
        assert tab.overall_progress_widget.progress_bar.maximum() == 100
        assert tab.overall_progress_widget.progress_bar.value() == 42
        assert "Parsing" in tab.overall_progress_widget.status_label.text()

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
        status_before = tab.overall_progress_widget.status_label.text()
        tab._on_item_started(99)
        assert tab.overall_progress_widget.status_label.text() == status_before

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

    def test_cleanup_cancelled_shows_cancelled(self, tab):
        _mine(tab, _series(2))
        tab._on_item_started(0)
        tab._on_cancel_clicked()
        assert tab.cancel_button.text() == "Cancelling…"

        tab._on_worker_finished()

        assert tab.cancel_button.text() == "Cancel"
        assert tab.cancel_button.isEnabled()
        assert tab.overall_progress_widget.status_label.text() == "Cancelled"
        assert tab.overall_progress_widget.progress_bar.value() == 0
        assert tab.worker_thread is None
        assert tab._run_items == []
        assert not tab.mine_button.isHidden()
        assert tab.cancel_button.isHidden()

    def test_cleanup_success_pins_summary(self, tab):
        _mine(tab, _series(2))
        tab._on_item_finished(0, MagicMock(cards_created=3, new_words_found=4), None, 1)
        tab._on_item_finished(1, MagicMock(cards_created=2, new_words_found=2), None, 1)

        tab._on_worker_finished()

        assert tab.overall_progress_widget.progress_bar.value() == 100
        assert tab.overall_progress_widget.status_label.text() == "Complete — 5 cards created"

    def test_cleanup_failed_shows_failed(self, tab):
        """Run-level fatal (worker.error) must not render a success summary."""
        _mine(tab, _series(2))
        tab._on_run_error("stale dicts")

        tab._on_worker_finished()

        assert tab.overall_progress_widget.progress_bar.value() == 0
        assert tab.overall_progress_widget.status_label.text() == "Failed — see log"
        assert "stale dicts" in tab.log_widget.text_edit.toPlainText()


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

    def test_drop_subtitle_hints_no_path(self, tab):
        event = MagicMock()
        with patch(_URLS, return_value=[_url("/src/ep01.srt")]):
            tab.dropEvent(event)
        assert tab.volume_folder_selector.get_path() == ""
        assert "Subtitles tab" in tab.log_widget.text_edit.toPlainText()
        event.acceptProposedAction.assert_called_once()

    def test_drop_none_event_is_noop(self, tab):
        tab.dropEvent(None)  # must not raise
        assert tab.volume_folder_selector.get_path() == ""

    def test_drag_enter_accepts_folder_manga_novel_and_subtitle(self, tmp_path, tab):
        folder = tmp_path / "d"
        folder.mkdir()
        for name in (str(folder), "/src/a.mokuro", "/src/a.cbz", "/src/a.epub", "/src/a.srt"):
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

    The definition-pane ``lookup_fn`` is always wired from the worker's
    ``curation_processor`` (like novels/subtitles). The media context falls back
    to ``None`` when there is no published document, a book-kind document, or a
    volume with no page images; with no worker at all both are ``None``.
    """

    def test_build_curation_context_is_none_none_without_worker(self, tab):
        # No worker → no processor → no lookup_fn either.
        assert tab._build_curation_context() == (None, None)

    def test_manga_document_yields_page_units_context(self, tab):
        _mine(tab, [_make_ref()])
        doc = _manga_document()
        tab.worker_thread.curation_document = doc

        ctx, lookup_fn = tab._build_curation_context()

        # Definition pane wired from the worker's processor.
        assert lookup_fn is tab.worker_thread.curation_processor.offline_lookup_fn
        assert ctx is not None
        assert ctx.video_file is None
        # Every unit is mapped by index — including imageless ones, so
        # unmatched pages still show their page label in the placeholder.
        assert ctx.page_units == {0: doc.units[0], 1: doc.units[1]}

    def test_none_document_falls_back(self, tab):
        _mine(tab, [_make_ref()])
        tab.worker_thread.curation_document = None
        ctx, lookup_fn = tab._build_curation_context()
        assert ctx is None
        assert lookup_fn is tab.worker_thread.curation_processor.offline_lookup_fn

    def test_book_kind_falls_back(self, tab):
        _mine(tab, [_make_ref()])
        tab.worker_thread.curation_document = _manga_document(kind="book")
        ctx, lookup_fn = tab._build_curation_context()
        assert ctx is None
        assert lookup_fn is tab.worker_thread.curation_processor.offline_lookup_fn

    def test_imageless_volume_falls_back(self, tab):
        _mine(tab, [_make_ref()])
        tab.worker_thread.curation_document = _manga_document(with_images=False)
        ctx, lookup_fn = tab._build_curation_context()
        assert ctx is None
        assert lookup_fn is tab.worker_thread.curation_processor.offline_lookup_fn


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

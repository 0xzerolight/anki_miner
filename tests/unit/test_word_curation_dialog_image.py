"""Tests for the WordCurationDialog manga page-image pane.

Mirrors test_word_curation_dialog_media.py's ``_select_row``/``_fire_timer``
helpers. The happy-path tests replace ``run_off_thread`` with a synchronous
or deferred stub; the teardown tests use the REAL off-thread worker to
exercise the join-and-detach path that a stub cannot reach.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtGui import QColor, QImage

from anki_miner.gui.widgets.dialogs import word_curation_dialog as wcd
from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    CurationMediaContext,
    WordCurationDialog,
)
from anki_miner.models import TokenizedWord
from anki_miner.services.reading.models import ImageRef, ReadingUnit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_word(lemma: str, unit_index: int) -> TokenizedWord:
    # Reading-path words carry the unit index as a dummy start_time
    # (subtitle_parser stamps start_time = float(unit.index)).
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="よみ",
        sentence=f"{lemma}のテスト",
        start_time=float(unit_index),
        end_time=float(unit_index),
        duration=0.0,
        pos="名詞",
    )


def _make_units(*specs: tuple[int, str | None, str]) -> dict[int, ReadingUnit]:
    """Build page_units from (index, image_name_or_None, label) specs."""
    units: dict[int, ReadingUnit] = {}
    for index, image_name, label in specs:
        ref = ImageRef(Path(f"/pages/{image_name}")) if image_name else None
        units[index] = ReadingUnit(
            text=f"unit{index}",
            index=index,
            location_label=label,
            image_ref=ref,
            block_box=(index, index, index + 10, index + 10),
        )
    return units


def _image_context(units: dict[int, ReadingUnit]) -> CurationMediaContext:
    return CurationMediaContext(video_file=None, subtitle_entries=[], page_units=units)


def _qimage(width: int = 8, height: int = 8) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(255, 255, 255))
    return image


def _select_row(dialog: WordCurationDialog, row: int) -> None:
    dialog.table.setCurrentCell(row, 0)
    dialog._on_row_focus_changed()


def _fire_timer(dialog: WordCurationDialog) -> None:
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()


@pytest.fixture()
def sync_off_thread(monkeypatch):
    """Replace run_off_thread with a synchronous stub; returns the loader mock.

    ``load_page_qimage`` is also stubbed (per-ref distinct sizes) so no real
    decode happens; the loader mock's call list identifies cache behaviour.
    """
    loader = MagicMock(side_effect=lambda ref: _qimage(8 + len(str(ref.source)) % 3, 8))
    monkeypatch.setattr(wcd, "load_page_qimage", loader)

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - mirrors SingleCallWorker's error path
            if on_error is not None:
                on_error(str(exc))
            return MagicMock()
        on_done(result)
        return MagicMock()

    monkeypatch.setattr(wcd, "run_off_thread", fake_run_off_thread)
    return loader


@pytest.fixture()
def deferred_off_thread(monkeypatch):
    """Capture (work, on_done, on_error) without running them — for overlap tests."""
    loader = MagicMock(side_effect=lambda ref: _qimage())
    monkeypatch.setattr(wcd, "load_page_qimage", loader)
    pending: list[tuple] = []

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        pending.append((work, on_done, on_error))
        return MagicMock()

    monkeypatch.setattr(wcd, "run_off_thread", fake_run_off_thread)
    return pending


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_no_page_units_no_image_pane(self, qtbot):
        dlg = WordCurationDialog([_make_word("食べる", 0)])
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "page_image_view")
        assert not dlg._show_image

    def test_media_context_without_page_units_no_image_pane(self, qtbot):
        ctx = CurationMediaContext(video_file=None, subtitle_entries=[])
        dlg = WordCurationDialog([_make_word("食べる", 0)], media_context=ctx)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "page_image_view")


# ---------------------------------------------------------------------------
# Focus -> page shown
# ---------------------------------------------------------------------------


class TestFocusShowsPage:
    def test_focus_shows_page_box_caption(self, qtbot, sync_off_thread):
        units = _make_units((0, "001.png", "p.1"), (1, "002.png", "p.2"))
        words = [_make_word("食べる", 0), _make_word("走る", 1)]
        dlg = WordCurationDialog(words, media_context=_image_context(units))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        view = dlg.page_image_view
        assert view.current_pixmap is not None
        assert view.current_box == units[0].block_box
        assert view.caption_text == "p.1"

    def test_image_only_wiring_uses_real_selection_signal(self, qtbot, sync_off_thread):
        # Image-only dialog (no player/dict/candidates): the itemSelectionChanged
        # connect must include _show_image, else no image ever loads. Drive the
        # REAL signal via setCurrentCell — no direct _on_row_focus_changed call.
        units = _make_units((0, "001.png", "p.1"))
        dlg = WordCurationDialog([_make_word("食べる", 0)], media_context=_image_context(units))
        qtbot.addWidget(dlg)

        dlg.table.setCurrentCell(0, 0)
        assert dlg._focus_timer.isActive(), "row focus was not wired for an image-only dialog"
        _fire_timer(dlg)
        assert dlg.page_image_view.current_pixmap is not None

    def test_candidate_pick_path_requests_new_unit(self, qtbot, sync_off_thread):
        # _on_candidate_chosen funnels through _preview_scene (deferred via
        # QTimer.singleShot(0)); calling _preview_scene directly matches the
        # deferred lambda body.
        units = _make_units((0, "001.png", "p.1"), (5, "003.png", "p.3"))
        dlg = WordCurationDialog([_make_word("食べる", 0)], media_context=_image_context(units))
        qtbot.addWidget(dlg)

        dlg._preview_scene(5.0)
        assert dlg.page_image_view.caption_text == "p.3"
        assert dlg.page_image_view.current_box == units[5].block_box


# ---------------------------------------------------------------------------
# Placeholders
# ---------------------------------------------------------------------------


class TestPlaceholders:
    def test_unknown_unit_index_shows_message(self, qtbot, sync_off_thread):
        units = _make_units((0, "001.png", "p.1"))
        dlg = WordCurationDialog([_make_word("食べる", 7)], media_context=_image_context(units))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert dlg.page_image_view.current_pixmap is None
        assert dlg.page_image_view.current_message != ""
        sync_off_thread.assert_not_called()

    def test_imageless_unit_shows_message_with_label(self, qtbot, sync_off_thread):
        # An unmatched page keeps its unit (image_ref=None) so the placeholder
        # still names the page.
        units = _make_units((0, None, "p.9"))
        dlg = WordCurationDialog([_make_word("食べる", 0)], media_context=_image_context(units))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert dlg.page_image_view.current_message != ""
        assert dlg.page_image_view.caption_text == "p.9"

    def test_loader_error_shows_error_placeholder(self, qtbot, sync_off_thread):
        sync_off_thread.side_effect = ValueError("boom")
        units = _make_units((0, "001.png", "p.1"))
        dlg = WordCurationDialog([_make_word("食べる", 0)], media_context=_image_context(units))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert dlg.page_image_view.current_pixmap is None
        assert dlg.page_image_view.current_message != ""


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class TestCache:
    def test_same_page_second_word_hits_cache(self, qtbot, sync_off_thread):
        units = _make_units((0, "001.png", "p.1"), (1, "001.png", "p.1"))
        words = [_make_word("食べる", 0), _make_word("走る", 1)]
        dlg = WordCurationDialog(words, media_context=_image_context(units))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)
        _select_row(dlg, 1)
        _fire_timer(dlg)

        assert sync_off_thread.call_count == 1

    def test_lru_evicts_past_cap(self, qtbot, sync_off_thread):
        specs = [(i, f"{i:03d}.png", f"p.{i + 1}") for i in range(5)]
        units = _make_units(*specs)
        words = [_make_word(f"word{i}", i) for i in range(5)]
        dlg = WordCurationDialog(words, media_context=_image_context(units))
        qtbot.addWidget(dlg)

        for i in range(5):
            dlg._request_page_image(i)

        assert len(dlg._page_cache) == wcd._PAGE_CACHE_CAP == 4
        first_ref = units[0].image_ref
        assert first_ref not in dlg._page_cache  # oldest evicted

        # Re-requesting the evicted page reloads it.
        dlg._request_page_image(0)
        assert sync_off_thread.call_count == 6


# ---------------------------------------------------------------------------
# Stale-guard (generation counter)
# ---------------------------------------------------------------------------


class TestStaleGuard:
    def test_stale_result_dropped(self, qtbot, deferred_off_thread):
        units = _make_units((0, "001.png", "p.1"), (1, "002.png", "p.2"))
        words = [_make_word("食べる", 0), _make_word("走る", 1)]
        dlg = WordCurationDialog(words, media_context=_image_context(units))
        qtbot.addWidget(dlg)

        dlg._request_page_image(0)  # gen 1, load pending
        dlg._request_page_image(1)  # gen 2, load pending

        # Deliver the FIRST (stale) load, then the current one.
        work0, on_done0, _ = deferred_off_thread[0]
        on_done0(work0())
        assert dlg.page_image_view.current_pixmap is None  # stale result dropped

        work1, on_done1, _ = deferred_off_thread[1]
        on_done1(work1())
        assert dlg.page_image_view.caption_text == "p.2"

    def test_cache_hit_supersedes_inflight_miss(self, qtbot, deferred_off_thread):
        # A cache hit must ALSO bump the generation, or a slower earlier miss
        # would clobber the just-shown page when it lands.
        units = _make_units((0, "001.png", "p.1"), (1, "002.png", "p.2"))
        words = [_make_word("食べる", 0), _make_word("走る", 1)]
        dlg = WordCurationDialog(words, media_context=_image_context(units))
        qtbot.addWidget(dlg)

        dlg._request_page_image(0)  # miss, pending
        work0, on_done0, _ = deferred_off_thread[0]
        on_done0(work0())  # delivered -> page 1 shown + cached
        assert dlg.page_image_view.caption_text == "p.1"

        dlg._request_page_image(1)  # miss, pending (slow)
        dlg._request_page_image(0)  # cache HIT -> shows p.1, must supersede

        work1, on_done1, _ = deferred_off_thread[1]
        on_done1(work1())  # the slow miss lands late
        assert dlg.page_image_view.caption_text == "p.1", "in-flight miss clobbered a newer cache hit"

    def test_stale_error_dropped(self, qtbot, deferred_off_thread):
        units = _make_units((0, "001.png", "p.1"), (1, "002.png", "p.2"))
        dlg = WordCurationDialog([_make_word("食べる", 0)], media_context=_image_context(units))
        qtbot.addWidget(dlg)

        dlg._request_page_image(0)
        dlg._request_page_image(1)

        _, _, on_error0 = deferred_off_thread[0]
        on_error0("boom")  # stale error must not repaint the pane
        assert dlg.page_image_view.current_message == ""


# ---------------------------------------------------------------------------
# Teardown — real off-thread workers (no stub)
# ---------------------------------------------------------------------------


class TestTeardown:
    def test_close_mid_load_detaches_laggard(self, qtbot, monkeypatch):
        """Reject while a decode is in flight: worker joined/detached, no abort."""
        release = threading.Event()
        started = threading.Event()

        def blocking_loader(ref):
            started.set()
            release.wait(timeout=5)
            return _qimage()

        monkeypatch.setattr(wcd, "load_page_qimage", blocking_loader)

        captured: list = []
        real_run_off_thread = wcd.run_off_thread

        def capturing(parent, work, on_done, on_error=None, **kwargs):
            worker = real_run_off_thread(parent, work, on_done, on_error, **kwargs)
            captured.append(worker)
            return worker

        monkeypatch.setattr(wcd, "run_off_thread", capturing)

        units = _make_units((0, "001.png", "p.1"))
        dlg = WordCurationDialog([_make_word("食べる", 0)], media_context=_image_context(units))
        qtbot.addWidget(dlg)

        try:
            dlg._request_page_image(0)
            assert captured, "no worker dispatched"
            worker = captured[0]
            assert started.wait(timeout=5), "loader never started"

            dlg.reject()  # finished -> _stop_player -> join (times out) + detach

            assert dlg._closing
            assert worker.parent() is None, "in-flight worker was not detached from the dying dialog"
        finally:
            # Unblock + join in finally so an assertion failure can't leave a
            # running QThread behind (leaked-QThread SIGABRT class).
            release.set()
            for worker in captured:
                worker.wait(5000)

    def test_post_drain_dispatch_blocked(self, qtbot, monkeypatch):
        """After reject, a pending timer/singleShot tick must not dispatch."""
        loader = MagicMock(side_effect=lambda ref: _qimage())
        monkeypatch.setattr(wcd, "load_page_qimage", loader)
        dispatch = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(wcd, "run_off_thread", dispatch)

        units = _make_units((0, "001.png", "p.1"))
        dlg = WordCurationDialog([_make_word("食べる", 0)], media_context=_image_context(units))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)  # arms the focus timer (pending tick)
        dlg.reject()

        assert dlg._closing
        assert not dlg._focus_timer.isActive()  # pins _focus_timer.stop()

        # Simulate the pending 120ms tick and the singleShot(0) lambda body
        # firing after the drain but before deleteLater is processed.
        dlg._on_focus_timer_fired()
        dlg._preview_scene(0.0)

        dispatch.assert_not_called()

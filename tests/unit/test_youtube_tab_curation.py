"""YouTubeTab word-curation wiring with player + dictionary parity (Issue #65).

Mirrors ``test_batch_processing_tab_curation.py``: the tab gains a
"Review words before mining" checkbox that gates whether the queue worker
gets a curation callback, a ``_build_curation_context`` override that sources
the embedded player + offline dictionary lookup from the live worker, and a
Stop handler that releases an open curation dialog.

Qt threads are never started — ``YouTubeProbeWorker`` and ``YouTubeQueueWorker``
are class-level patched so ``start()`` is a no-op and constructor kwargs can be
inspected.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.youtube_tab import YouTubeTab
from anki_miner.models.youtube import VideoInfo

# QApplication instance needed for any widget test.
_app = QApplication.instance() or QApplication([])


def _make_video_info() -> VideoInfo:
    return VideoInfo(
        video_id="abc123",
        title="Sample Video",
        duration_s=600,
        has_manual_ja_subs=True,
        has_auto_ja_subs=False,
        thumbnail_url=None,
        uploader="Uploader",
        is_live=False,
        is_age_restricted=False,
    )


@pytest.fixture
def tab(test_config: AnkiMinerConfig):
    """A YouTubeTab with patched probe/queue worker classes (no real threads)."""
    cfg = replace(test_config, youtube_max_duration_s=7200, youtube_cookies_from_browser=None)

    probe_patch = patch("anki_miner.gui.widgets.youtube_tab.YouTubeProbeWorker", autospec=False)
    queue_patch = patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker", autospec=False)
    with probe_patch as probe_cls, queue_patch as queue_cls:
        probe_cls.side_effect = lambda *a, **kw: MagicMock(name="ProbeWorker")
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")

        widget = YouTubeTab(
            config=cfg,
            processor=MagicMock(name="EpisodeProcessor"),
            fetcher=MagicMock(name="Fetcher"),
            presenter=MagicMock(name="Presenter"),
        )
        widget._queue_worker_cls = queue_cls  # type: ignore[attr-defined]
        try:
            yield widget
        finally:
            widget.deleteLater()


def _add_ready_item(tab, url: str = "https://www.youtube.com/watch?v=abc"):
    tab.url_edit.setText(url)
    tab._on_add_clicked()
    item = tab._queue.all_items()[-1]
    tab._on_probe_done(item, _make_video_info())
    return item


def test_checkbox_default_unchecked(tab):
    assert tab.review_words_checkbox.isChecked() is False


def test_run_callback_follows_checkbox(tab):
    queue_cls = tab._queue_worker_cls

    # Checked → the bound bridge is handed to the worker.
    _add_ready_item(tab, "https://youtu.be/v1")
    tab.review_words_checkbox.setChecked(True)
    tab._on_mine_clicked()
    # Bound methods compare by ``==`` (fresh wrapper per attribute access).
    assert queue_cls.call_args.kwargs["curation_callback"] == tab._curation_bridge

    # Reset worker handle to allow another run.
    tab.worker_thread = None

    # Unchecked → no callback.
    tab.review_words_checkbox.setChecked(False)
    tab._on_mine_clicked()
    assert queue_cls.call_args.kwargs["curation_callback"] is None


def test_build_curation_context_no_worker_returns_none(tab):
    tab.worker_thread = None
    assert tab._build_curation_context() == (None, None)


def test_build_curation_context_reads_worker_attrs(tab, tmp_path):
    subs = tmp_path / "v1.srt"
    subs.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nテスト\n",
        encoding="utf-8",
    )
    video = tmp_path / "v1.mp4"
    video.touch()

    lookup = MagicMock(name="lookup_all_offline")
    proc = SimpleNamespace(definition_service=SimpleNamespace(lookup_all_offline=lookup))
    tab.worker_thread = SimpleNamespace(
        _curation_processor=proc,
        _curation_video=video,
        _curation_subtitle=subs,
        _curation_offset=4.0,
    )

    with patch("anki_miner.gui.widgets.youtube_tab.resolve_ffprobe", return_value="ffprobe"):
        media_context, lookup_fn = tab._build_curation_context()

    assert lookup_fn is lookup
    assert media_context is not None
    assert media_context.video_file == video
    assert media_context.offset == 4.0
    assert media_context.subtitle_entries  # parsed at least one entry


def test_build_curation_context_parse_error_returns_none_context(tab, tmp_path):
    subs = tmp_path / "v1.srt"
    subs.touch()
    video = tmp_path / "v1.mp4"
    video.touch()

    lookup = MagicMock(name="lookup_all_offline")
    proc = SimpleNamespace(definition_service=SimpleNamespace(lookup_all_offline=lookup))
    tab.worker_thread = SimpleNamespace(
        _curation_processor=proc,
        _curation_video=video,
        _curation_subtitle=subs,
        _curation_offset=0.0,
    )

    mock_parser = MagicMock()
    mock_parser.return_value.parse_raw_entries.side_effect = RuntimeError("bad subs")
    with patch("anki_miner.gui.widgets.youtube_tab.SubtitleParserService", mock_parser):
        media_context, lookup_fn = tab._build_curation_context()

    assert media_context is None
    assert lookup_fn is lookup


def test_stop_releases_active_curation_dialog(tab):
    with patch.object(tab, "_cancel_active_curation_dialog") as cancel:
        tab.worker_thread = MagicMock(name="QueueWorker")
        tab._on_stop_all_clicked()
        cancel.assert_called_once()
        tab.worker_thread.cancel.assert_called_once()


def test_shutdown_releases_curation_before_joining_worker(tab):
    """App close must release an open curation dialog before the unbounded
    worker.wait(), else a worker parked in _curation_event.wait() hangs the
    GUI forever (Issue #65). cancel() alone only sets _cancel_event."""
    with patch.object(tab, "_cancel_active_curation_dialog") as cancel:
        worker = MagicMock(name="QueueWorker")
        tab.worker_thread = worker
        tab.shutdown()
        cancel.assert_called_once()
        worker.cancel.assert_called_once()
        worker.wait.assert_called_once()


def test_shutdown_rejects_open_dialog_so_blocked_worker_resumes(tab):
    """shutdown() drives the real release path: an open dialog is rejected and
    _curation_cancelled is set, which (via _on_curation_requested's finally,
    covered in test_mining_tab_base_curation) sets _curation_event so the
    parked worker unblocks."""
    dialog = MagicMock(name="WordCurationDialog")
    tab._active_curation_dialog = dialog
    tab.worker_thread = MagicMock(name="QueueWorker")
    tab.shutdown()
    dialog.reject.assert_called_once()
    assert tab._curation_cancelled is True


def test_shutdown_unblocks_worker_parked_at_curation_gate(tab):
    """shutdown() must not deadlock on a worker parked in _curation_event.wait().

    The dialog-release path (Issue #65) only helps once the dialog exists. If
    the worker emitted _curation_requested but the queued GUI slot has not run
    yet, blocking in worker.wait() would deadlock: this GUI thread is the only
    one that could run the slot. shutdown() must poison the gate before joining
    (T-01)."""
    from PyQt6.QtCore import Qt

    results: list = []
    # DirectConnection probe fires on the bridge thread at emit time, right
    # before it parks. The queued _on_curation_requested slot itself never
    # runs because this (GUI) thread does not spin its event loop — exactly
    # the app-close scenario.
    reached_gate = threading.Event()
    tab._curation_requested.connect(lambda words: reached_gate.set(), Qt.ConnectionType.DirectConnection)
    bridge_thread = threading.Thread(
        target=lambda: results.append(tab._curation_bridge(["w1"])),
        daemon=True,
    )
    bridge_thread.start()
    assert reached_gate.wait(2.0), "bridge thread never emitted the curation request"
    time.sleep(0.05)  # let it advance the final step into _curation_event.wait()
    assert bridge_thread.is_alive(), "bridge thread should be parked at the gate"

    worker = MagicMock(name="QueueWorker")

    def _join_like_qthread_wait(*args, **kwargs):
        # QThread.wait() joins the worker thread; the parked bridge stands in
        # for the real worker body. Bounded so an unfixed deadlock fails fast.
        bridge_thread.join(timeout=2.0)
        return not bridge_thread.is_alive()

    worker.wait.side_effect = _join_like_qthread_wait
    tab.worker_thread = worker

    tab.shutdown()

    bridge_thread.join(timeout=1.0)
    assert not bridge_thread.is_alive(), "worker was never released from the curation gate"
    assert results == [None]  # released as cancelled, not with a selection
    assert tab.worker_thread is None

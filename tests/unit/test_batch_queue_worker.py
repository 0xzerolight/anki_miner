"""BatchQueueWorkerThread curation wiring (Issue #60) and error routing (Issue #51)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread
from anki_miner.models.batch_queue import BatchQueue, QueueItemStatus
from anki_miner.models.processing import ProcessingResult


def test_curation_attrs_use_item_offset_and_callback_forwarded(tmp_path):
    cb = MagicMock(name="curation_callback")
    captured = []

    pair = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")

    proc = MagicMock()

    def fake_process(video, subtitle, preview_mode, progress_callback, curation_callback=None):
        captured.append(
            {
                "offset": worker._curation_offset,
                "video": worker._curation_video,
                "processor": worker.curation_processor,
                "callback": curation_callback,
            }
        )
        return SimpleNamespace(cards_created=0)

    proc.process_episode.side_effect = fake_process

    item = SimpleNamespace(
        id="i1",
        display_name="Show",
        subtitle_offset=3.0,
        anime_folder=tmp_path / "anime",
        subtitle_folder=tmp_path / "subs",
    )
    queue = MagicMock()
    queue.pending_count = 1
    queue.get_next_pending.side_effect = [item, None]

    config = AnkiMinerConfig()
    worker = BatchQueueWorkerThread(queue, config, MagicMock(), None, curation_callback=cb)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=proc,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    assert captured, "process_episode was not called"
    assert captured[0]["offset"] == 3.0
    assert captured[0]["video"] == pair.video
    assert captured[0]["processor"] is proc
    assert captured[0]["callback"] is cb


# ---------------------------------------------------------------------------
# Helpers shared by Issue #51 tests
# ---------------------------------------------------------------------------


def _make_worker_with_queue(queue: BatchQueue) -> BatchQueueWorkerThread:
    """Build a BatchQueueWorkerThread around a real BatchQueue."""
    return BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock())


def _wire_status_slots(worker: BatchQueueWorkerThread, _queue: BatchQueue) -> dict:
    """Connect signals to dicts that capture emissions, mirroring GUI slot behaviour.

    Like BatchProcessingTab's slots, these are render-only: the worker owns all
    QueueItem status writes during a run (see BatchQueueWorkerThread.run), so
    capturing without mutating exercises the production ownership model.
    """
    results: dict = {"completed": [], "failed": [], "finished": []}

    def on_completed(item_id: str, cards: int) -> None:
        results["completed"].append((item_id, cards))

    def on_failed(item_id: str, msg: str) -> None:
        results["failed"].append((item_id, msg))

    def on_finished(total: int) -> None:
        results["finished"].append(total)

    worker.item_completed.connect(on_completed)
    worker.item_failed.connect(on_failed)
    worker.queue_finished.connect(on_finished)
    return results


def _failed_result() -> ProcessingResult:
    return ProcessingResult(
        total_words_found=0,
        new_words_found=0,
        cards_created=0,
        errors=["Error: deck missing"],
    )


def _ok_result(cards: int = 3) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=10,
        new_words_found=cards,
        cards_created=cards,
    )


# ---------------------------------------------------------------------------
# Issue #51 tests
# ---------------------------------------------------------------------------


def test_all_pairs_failed_emits_item_failed(tmp_path):
    """All pairs failing → item_failed emitted; item_completed not emitted; queue total 0."""
    pair1 = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))
    pair2 = SimpleNamespace(video=Path("/tmp/ep2.mkv"), subtitle=Path("/tmp/ep2.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "anime", tmp_path / "subs", "Show")

    proc = MagicMock()
    proc.process_episode.side_effect = [_failed_result(), _failed_result()]

    worker = _make_worker_with_queue(queue)
    results = _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=proc,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair1, pair2],
        ),
    ):
        worker.run()

    assert results["completed"] == [], "item_completed should NOT be emitted on full failure"
    assert len(results["failed"]) == 1, "item_failed should be emitted once"
    _item_id, msg = results["failed"][0]
    assert "2/2 episodes failed" in msg
    assert "ep1.mkv" in msg
    assert "Error: deck missing" in msg
    assert results["finished"] == [0], "queue_finished should emit 0 total cards"


def test_partial_failure_emits_item_failed_with_partial_cards(tmp_path):
    """First pair succeeds, second fails → item_failed with partial count; queue total includes successes."""
    pair1 = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))
    pair2 = SimpleNamespace(video=Path("/tmp/ep2.mkv"), subtitle=Path("/tmp/ep2.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "anime", tmp_path / "subs", "Show")

    proc = MagicMock()
    proc.process_episode.side_effect = [_ok_result(cards=3), _failed_result()]

    worker = _make_worker_with_queue(queue)
    results = _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=proc,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair1, pair2],
        ),
    ):
        worker.run()

    assert results["completed"] == [], "item_completed should NOT be emitted on partial failure"
    assert len(results["failed"]) == 1
    _item_id, msg = results["failed"][0]
    assert "1/2 episodes failed" in msg
    # Partial cards still count toward queue total
    assert results["finished"] == [3], "queue_finished should include cards from successful pairs"


def test_all_pairs_succeed_emits_item_completed(tmp_path):
    """Regression: all pairs succeed → item_completed with total cards; item_failed not emitted."""
    pair1 = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))
    pair2 = SimpleNamespace(video=Path("/tmp/ep2.mkv"), subtitle=Path("/tmp/ep2.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "anime", tmp_path / "subs", "Show")

    proc = MagicMock()
    proc.process_episode.side_effect = [_ok_result(cards=2), _ok_result(cards=3)]

    worker = _make_worker_with_queue(queue)
    results = _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=proc,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair1, pair2],
        ),
    ):
        worker.run()

    assert results["failed"] == [], "item_failed should NOT be emitted on full success"
    assert len(results["completed"]) == 1
    _item_id, cards = results["completed"][0]
    assert cards == 5
    assert results["finished"] == [5]


# ---------------------------------------------------------------------------
# Status-race regression tests (T-20): worker owns QueueItem status writes.
# These wire capture-only handlers (no status mutation) to simulate a stalled
# GUI event loop whose queued slots have not run yet. Each carries a watchdog
# that cancels the worker if the bug re-picks an item, so a regression fails
# by assertion instead of hanging the suite.
# ---------------------------------------------------------------------------


def _wire_capture_only(worker: BatchQueueWorkerThread) -> dict:
    """Capture signal emissions WITHOUT mirroring any GUI status writes."""
    results: dict = {"started": [], "completed": [], "failed": [], "finished": []}
    worker.item_started.connect(lambda item_id, _name: results["started"].append(item_id))
    worker.item_completed.connect(lambda item_id, cards: results["completed"].append((item_id, cards)))
    worker.item_failed.connect(lambda item_id, msg: results["failed"].append((item_id, msg)))
    worker.queue_finished.connect(lambda total: results["finished"].append(total))
    return results


def test_item_processed_exactly_once_when_gui_status_write_delayed(tmp_path):
    """Regression: with GUI status slots delayed, the finished item must not
    be re-picked as still-PENDING and processed again."""
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "anime", tmp_path / "subs", "Show")
    item = queue.get_all_items()[0]

    proc = MagicMock()
    proc.process_episode.return_value = _ok_result(cards=2)

    worker = _make_worker_with_queue(queue)
    results = _wire_capture_only(worker)
    # Watchdog: cancel on a second pick so a regression terminates.
    worker.item_started.connect(lambda *_: worker.cancel() if len(results["started"]) >= 2 else None)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=proc,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    assert results["started"] == [item.id], "item must be picked exactly once"
    assert results["completed"] == [(item.id, 2)], "item_completed must fire exactly once"
    assert results["failed"] == []
    assert proc.process_episode.call_count == 1
    assert item.status == QueueItemStatus.COMPLETED
    assert item.cards_created == 2


def test_fast_fail_item_fails_exactly_once_when_gui_status_write_delayed(tmp_path):
    """Regression: a fast-failing item ("No matching pairs" raises within the
    same loop iteration) must not hot-spin re-failing while GUI writes lag."""
    queue = BatchQueue()
    queue.add_item(tmp_path / "anime", tmp_path / "subs", "Show")
    item = queue.get_all_items()[0]

    worker = _make_worker_with_queue(queue)
    results = _wire_capture_only(worker)
    # Watchdog: cancel on a second failure so a regression terminates.
    worker.item_failed.connect(lambda *_: worker.cancel() if len(results["failed"]) >= 2 else None)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=MagicMock(),
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[],
        ),
    ):
        worker.run()

    assert len(results["failed"]) == 1, "item_failed must fire exactly once"
    assert "No matching video/subtitle pairs found" in results["failed"][0][1]
    assert results["completed"] == []
    assert item.status == QueueItemStatus.ERROR
    assert item.error_message == "No matching video/subtitle pairs found"


def test_worker_marks_item_processing_at_pick_time(tmp_path):
    """The worker itself (not a GUI slot) moves the item PENDING -> PROCESSING
    before work starts, and to COMPLETED when it finishes."""
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "anime", tmp_path / "subs", "Show")
    item = queue.get_all_items()[0]

    seen_during_processing: list[QueueItemStatus] = []

    proc = MagicMock()

    def fake_process(*_args, **_kwargs):
        seen_during_processing.append(item.status)
        return _ok_result(cards=1)

    proc.process_episode.side_effect = fake_process

    worker = _make_worker_with_queue(queue)
    _wire_capture_only(worker)
    # Watchdog: stop after the first completion so a regression terminates.
    worker.item_completed.connect(lambda *_: worker.cancel())

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=proc,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    assert seen_during_processing == [QueueItemStatus.PROCESSING]
    assert item.status == QueueItemStatus.COMPLETED


# ---------------------------------------------------------------------------
# Cancellation tests (T-21): an interrupted item must never read COMPLETED.
# ---------------------------------------------------------------------------


def test_cancel_mid_item_does_not_emit_item_completed(tmp_path):
    """Regression: cancel between pairs (1 of 3 processed) must not fall
    through to item_completed; the partially processed item is not COMPLETED."""
    pair1 = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))
    pair2 = SimpleNamespace(video=Path("/tmp/ep2.mkv"), subtitle=Path("/tmp/ep2.ass"))
    pair3 = SimpleNamespace(video=Path("/tmp/ep3.mkv"), subtitle=Path("/tmp/ep3.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "anime", tmp_path / "subs", "Show")
    item = queue.get_all_items()[0]

    proc = MagicMock()

    def cancel_during_first_pair(*_args, **_kwargs):
        worker.cancel()  # user hits Cancel while pair 1 is processing
        return _ok_result(cards=2)

    proc.process_episode.side_effect = cancel_during_first_pair

    worker = _make_worker_with_queue(queue)
    results = _wire_capture_only(worker)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=proc,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair1, pair2, pair3],
        ),
    ):
        worker.run()

    assert proc.process_episode.call_count == 1, "pairs 2-3 must not run after cancel"
    assert results["completed"] == [], "interrupted item must not emit item_completed"
    assert results["failed"] == [], "cancellation is not an item error"
    assert item.status != QueueItemStatus.COMPLETED
    assert item.status == QueueItemStatus.PENDING, "interrupted item returns to PENDING"
    # Cards created before the cancel exist in Anki and count toward the total.
    assert results["finished"] == [2]


def test_cancel_propagates_to_current_processor():
    """cancel() must forward to the in-flight EpisodeProcessor."""
    worker = BatchQueueWorkerThread(MagicMock(), AnkiMinerConfig(), MagicMock())
    proc = MagicMock()
    worker._current_processor = proc

    worker.cancel()

    proc.cancel.assert_called_once()
    assert worker.is_cancelled


def test_cancel_without_current_processor_does_not_raise():
    """cancel() before any item started (no processor yet) is safe."""
    worker = BatchQueueWorkerThread(MagicMock(), AnkiMinerConfig(), MagicMock())

    worker.cancel()

    assert worker.is_cancelled


def test_cancel_before_run_exits_at_loop_top():
    """Pre-cancelled worker exits at the loop top: queue_started(total) and
    queue_finished(0) still fire, but no item is ever picked."""
    queue = MagicMock()
    queue.pending_count = 3

    worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock())
    results = _wire_capture_only(worker)
    started_totals: list[int] = []
    worker.queue_started.connect(started_totals.append)

    worker.cancel()
    worker.run()

    assert started_totals == [3]
    queue.get_next_pending.assert_not_called()
    assert results["started"] == []
    assert results["completed"] == []
    assert results["failed"] == []
    assert results["finished"] == [0]


def test_setup_error_emits_item_failed(tmp_path):
    """process_episode raising SetupError causes item_failed to be emitted for that item."""
    pair = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")

    proc = MagicMock()
    proc.process_episode.side_effect = SetupError("note type not found")

    item = SimpleNamespace(
        id="i1",
        display_name="Show",
        subtitle_offset=0.0,
        anime_folder=tmp_path / "anime",
        subtitle_folder=tmp_path / "subs",
    )
    queue = MagicMock()
    queue.pending_count = 1
    queue.get_next_pending.side_effect = [item, None]

    config = AnkiMinerConfig()
    worker = BatchQueueWorkerThread(queue, config, MagicMock(), None)

    failed_emissions = []
    worker.item_failed.connect(lambda item_id, msg: failed_emissions.append((item_id, msg)))

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=proc,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    assert len(failed_emissions) == 1
    assert failed_emissions[0][0] == "i1"
    assert "note type not found" in failed_emissions[0][1]

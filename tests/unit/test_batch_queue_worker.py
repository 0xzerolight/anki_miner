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

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
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
        video_folder=tmp_path / "video",
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
    queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")

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
    queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")

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
    queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")

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
    queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")
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
    queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")
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
    queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")
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
    queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")
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
        video_folder=tmp_path / "video",
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


def test_mid_loop_raise_does_not_abort_remaining_pairs_or_lose_cards(tmp_path):
    """Regression: the new card-target preflight (#52) makes process_episode raise.

    A raise on pair 2 of 3 must NOT abort pairs 3.. nor discard the cards already
    created for pair 1 — without the per-pair guard the raise escaped to the outer
    except, marked the whole item ERROR, skipped the cards-counting, and never ran
    pair 3.
    """
    pair1 = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))
    pair2 = SimpleNamespace(video=Path("/tmp/ep2.mkv"), subtitle=Path("/tmp/ep2.ass"))
    pair3 = SimpleNamespace(video=Path("/tmp/ep3.mkv"), subtitle=Path("/tmp/ep3.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")
    item = queue.get_all_items()[0]

    proc = MagicMock()
    proc.process_episode.side_effect = [
        _ok_result(cards=3),
        SetupError("AnkiConnect unreachable"),
        _ok_result(cards=5),
    ]

    worker = _make_worker_with_queue(queue)
    results = _wire_status_slots(worker, queue)

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

    # All three pairs attempted — the raise on pair 2 did not abort pair 3.
    assert proc.process_episode.call_count == 3
    # Item marked failed (one pair failed) with the raised pair reported.
    assert len(results["failed"]) == 1
    _item_id, msg = results["failed"][0]
    assert "1/3 episodes failed" in msg
    assert "ep2.mkv" in msg
    assert "AnkiConnect unreachable" in msg
    assert results["completed"] == []
    # Cards from pairs 1 and 3 are preserved (3 + 5), not discarded by the raise.
    assert results["finished"] == [8]
    assert item.status == QueueItemStatus.ERROR


# ---------------------------------------------------------------------------
# Per-item processor close() between sequential items (Windows freeze fix)
# ---------------------------------------------------------------------------


def test_each_item_processor_closed(tmp_path):
    """A 2-item queue closes each item's processor; the prior one before the next is built."""
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")
    queue.add_item(tmp_path / "video2", tmp_path / "subs2", "Show2")

    proc1 = MagicMock(name="proc1")
    proc1.process_episode.return_value = _ok_result(cards=1)
    proc2 = MagicMock(name="proc2")
    proc2.process_episode.return_value = _ok_result(cards=1)

    order: list[str] = []
    proc1.close.side_effect = lambda: order.append("close1")
    proc2.close.side_effect = lambda: order.append("close2")

    built: list[MagicMock] = [proc1, proc2]

    def _build(*a, **k):
        proc = built.pop(0)
        order.append(f"build:{proc._mock_name}")
        return proc

    worker = _make_worker_with_queue(queue)
    _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            side_effect=_build,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    proc1.close.assert_called_once_with()
    proc2.close.assert_called_once_with()
    # proc1 is closed before proc2 is built; proc2 closed at the end.
    assert order == ["build:proc1", "close1", "build:proc2", "close2"], order


def test_processor_closed_on_exception_exit(tmp_path):
    """The current processor is closed even when run() exits via an exception path."""
    queue = BatchQueue()
    queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")

    proc = MagicMock(name="proc")

    worker = _make_worker_with_queue(queue)
    _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=proc,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            side_effect=RuntimeError("pairing exploded"),
        ),
    ):
        worker.run()

    # The per-item try/except marks the item ERROR; the finally still closes proc.
    proc.close.assert_called_once_with()


def test_close_failure_does_not_abort_queue(tmp_path):
    """A processor.close() that raises must not crash the queue loop."""
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")
    queue.add_item(tmp_path / "video2", tmp_path / "subs2", "Show2")

    proc1 = MagicMock(name="proc1")
    proc1.process_episode.return_value = _ok_result(cards=2)
    proc1.close.side_effect = RuntimeError("close boom")
    proc2 = MagicMock(name="proc2")
    proc2.process_episode.return_value = _ok_result(cards=3)

    built = [proc1, proc2]

    worker = _make_worker_with_queue(queue)
    results = _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            side_effect=lambda *a, **k: built.pop(0),
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    # Second item still processed despite first close() raising.
    assert results["finished"] == [5], results["finished"]


# ---------------------------------------------------------------------------
# Shared AnkiService across batch items (OVH-011/013)
# ---------------------------------------------------------------------------


def test_shared_anki_service_passed_to_each_processor(tmp_path):
    """A single AnkiService instance must be built once and passed via
    anki_service= to every create_episode_processor call in the run, so the
    vocab cache survives across all queue items."""
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")
    queue.add_item(tmp_path / "video2", tmp_path / "subs2", "Show2")

    proc = MagicMock()
    proc.process_episode.return_value = _ok_result(cards=1)

    captured_anki_services: list = []

    def _fake_create_ep(config, presenter, stats_service=None, anki_service=None, **kwargs):
        captured_anki_services.append(anki_service)
        return proc

    worker = _make_worker_with_queue(queue)
    _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            side_effect=_fake_create_ep,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    # create_episode_processor called once per item (2 items)
    assert len(captured_anki_services) == 2
    # Both calls received the SAME AnkiService instance
    assert captured_anki_services[0] is captured_anki_services[1]
    assert captured_anki_services[0] is not None


def test_batch_vocab_scan_at_most_once_across_items(tmp_path):
    """With a shared AnkiService, get_existing_vocabulary (findNotes) must be
    called AT MOST ONCE across all queue items in a batch run — not once per item.
    The second item hits the cache and never re-queries AnkiConnect."""
    from unittest.mock import MagicMock, patch

    from anki_miner.services.anki_service import AnkiService

    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")
    queue.add_item(tmp_path / "video2", tmp_path / "subs2", "Show2")

    # Track all AnkiService instances constructed during the run
    constructed_services: list[AnkiService] = []
    original_init = AnkiService.__init__

    def _tracking_init(self, config):
        original_init(self, config)
        constructed_services.append(self)

    def _tracking_get_vocab(self):
        # Simulate a populated response without HTTP by priming the cache directly
        self._existing_vocab_cache = {"既知"}
        return self._existing_vocab_cache

    proc = MagicMock()
    proc.process_episode.return_value = _ok_result(cards=1)

    worker = _make_worker_with_queue(queue)
    _wire_status_slots(worker, queue)

    with (
        patch.object(AnkiService, "__init__", _tracking_init),
        patch.object(AnkiService, "get_existing_vocabulary", _tracking_get_vocab),
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

    # Only ONE AnkiService must be constructed for the whole run
    assert len(constructed_services) == 1, f"Expected 1 AnkiService construction, got {len(constructed_services)}"


# ---------------------------------------------------------------------------
# 4.0: schema-staleness pre-loop gate — abort once, no per-item rows
# ---------------------------------------------------------------------------


def test_stale_dict_aborts_queue_once(qapp):
    """A stale enabled dict slot surfaces the error exactly once (no per-item
    failure rows, no items picked) and still emits queue_finished."""
    queue = MagicMock()
    queue.pending_count = 3
    config = AnkiMinerConfig()
    worker = BatchQueueWorkerThread(queue, config, MagicMock(), None)

    errors: list[str] = []
    item_started, item_completed, item_failed, finished = [], [], [], []
    worker.error.connect(errors.append)
    worker.item_started.connect(lambda *a: item_started.append(a))
    worker.item_completed.connect(lambda *a: item_completed.append(a))
    worker.item_failed.connect(lambda *a: item_failed.append(a))
    worker.queue_finished.connect(finished.append)

    with patch(
        "anki_miner.gui.workers.batch_queue_worker.stale_dict_reimport_error",
        return_value="Dictionary 'X' needs reimport (schema upgrade) — Settings → Dictionaries → Reimport All",
    ):
        worker.run()

    assert len(errors) == 1
    assert "Reimport All" in errors[0]
    # Abort-once: no item picked, no per-item rows.
    queue.get_next_pending.assert_not_called()
    assert item_started == [] and item_completed == [] and item_failed == []
    assert finished == [0]  # queue_finished(total_cards=0)


# ---------------------------------------------------------------------------
# G1: a setup failure OUTSIDE the per-item try must not abort the run() thread.
# Code before the item loop (stale-dict gate, AnkiService construction,
# get_next_pending) runs OUTSIDE the per-item ``try/except``; run() itself was
# ``try/finally`` with NO ``except``. An exception there (e.g. AnkiService
# raising ValueError on missing anki_fields) propagated straight out of the
# reimplemented QThread.run() → PyQt6 FATAL abort. run() must instead catch it,
# emit ``error`` then ``queue_finished``, and return normally.
# ---------------------------------------------------------------------------


def test_setup_failure_emits_error_and_queue_finished(qapp):
    """AnkiService construction raising is caught: error + queue_finished, no propagation."""
    queue = MagicMock()
    queue.pending_count = 2
    worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock(), None)

    errors: list[str] = []
    finished: list[int] = []
    worker.error.connect(errors.append)
    worker.queue_finished.connect(finished.append)

    with patch(
        "anki_miner.gui.workers.batch_queue_worker.AnkiService",
        side_effect=ValueError("Missing required field mappings: Expression"),
    ):
        worker.run()  # must NOT raise out of the reimplemented run()

    assert len(errors) == 1
    assert "Missing required field mappings" in errors[0]
    assert finished == [0]  # queue_finished(total_cards=0) even on setup failure
    # The item loop was never entered — the failure was before get_next_pending.
    queue.get_next_pending.assert_not_called()


# ---------------------------------------------------------------------------
# Shared lookup services across batch items (dict/pitch/frequency rebuild fix)
# ---------------------------------------------------------------------------


def _bundle_mock():
    bundle = MagicMock(name="shared_lookup")
    bundle.load_result.info = ["Frequency data loaded: 1 source(s), 3 entries"]
    bundle.load_result.warnings = ["some warning"]
    return bundle


def test_shared_lookup_services_passed_to_each_processor(tmp_path):
    """One SharedLookupServices bundle per run: built once, passed via
    shared_lookup= to every create_episode_processor call."""
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")
    queue.add_item(tmp_path / "video2", tmp_path / "subs2", "Show2")

    proc = MagicMock()
    proc.process_episode.return_value = _ok_result(cards=1)
    bundle = _bundle_mock()

    captured: list = []

    def _fake_create_ep(config, presenter, stats_service=None, anki_service=None, shared_lookup=None, **kwargs):
        captured.append(shared_lookup)
        return proc

    worker = _make_worker_with_queue(queue)
    _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_shared_lookup_services",
            return_value=bundle,
        ) as factory,
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            side_effect=_fake_create_ep,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    factory.assert_called_once()
    assert len(captured) == 2
    assert captured[0] is bundle
    assert captured[1] is bundle


def test_shared_lookup_services_closed_once_on_normal_exit(tmp_path):
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")

    proc = MagicMock()
    proc.process_episode.return_value = _ok_result(cards=1)
    bundle = _bundle_mock()

    worker = _make_worker_with_queue(queue)
    _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_shared_lookup_services",
            return_value=bundle,
        ),
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

    bundle.close.assert_called_once_with()


def test_shared_lookup_services_closed_on_exception_exit(tmp_path):
    """A processor-construction crash still closes the bundle (finally path)."""
    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")

    bundle = _bundle_mock()

    worker = _make_worker_with_queue(queue)
    _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_shared_lookup_services",
            return_value=bundle,
        ),
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            side_effect=RuntimeError("construction boom"),
        ),
    ):
        worker.run()  # must not raise; run() surfaces the error via signal

    bundle.close.assert_called_once_with()


def test_shared_lookup_services_closed_on_cancel(tmp_path):
    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")

    bundle = _bundle_mock()

    worker = _make_worker_with_queue(queue)
    _wire_status_slots(worker, queue)
    worker.cancel()  # cancelled before the loop starts

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_shared_lookup_services",
            return_value=bundle,
        ),
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
        ) as create_ep,
    ):
        worker.run()

    create_ep.assert_not_called()
    bundle.close.assert_called_once_with()


def test_shared_load_messages_surfaced_once_per_run(tmp_path):
    """The bundle's load_result info/warnings reach the presenter exactly once
    per run (previously: once per item via each create_episode_processor)."""
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")
    queue.add_item(tmp_path / "video2", tmp_path / "subs2", "Show2")

    proc = MagicMock()
    proc.process_episode.return_value = _ok_result(cards=1)
    bundle = _bundle_mock()

    worker = _make_worker_with_queue(queue)
    _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_shared_lookup_services",
            return_value=bundle,
        ),
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

    info_calls = [c.args[0] for c in worker.presenter.show_info.call_args_list]
    warning_calls = [c.args[0] for c in worker.presenter.show_warning.call_args_list]
    assert info_calls.count("Frequency data loaded: 1 source(s), 3 entries") == 1
    assert warning_calls.count("some warning") == 1

"""BatchQueueWorkerThread curation wiring (Issue #60) and error routing (Issue #51)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from anki_miner.config import AnkiMinerConfig
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
                "processor": worker._curation_processor,
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


def _wire_status_slots(worker: BatchQueueWorkerThread, queue: BatchQueue) -> dict:
    """Connect signals to dicts that capture emissions and mirror GUI slot behaviour.

    The worker loop calls get_next_pending() to advance the queue; without the
    GUI slots running (we're synchronous, no Qt event loop), we must set item
    status ourselves to avoid an infinite PENDING loop.
    """
    results: dict = {"completed": [], "failed": [], "finished": []}

    def on_started(item_id: str, _name: str) -> None:
        for item in queue.get_all_items():
            if item.id == item_id:
                item.status = QueueItemStatus.PROCESSING
                break

    def on_completed(item_id: str, cards: int) -> None:
        results["completed"].append((item_id, cards))
        for item in queue.get_all_items():
            if item.id == item_id:
                item.status = QueueItemStatus.COMPLETED
                item.cards_created = cards
                break

    def on_failed(item_id: str, msg: str) -> None:
        results["failed"].append((item_id, msg))
        for item in queue.get_all_items():
            if item.id == item_id:
                item.status = QueueItemStatus.ERROR
                item.error_message = msg
                break

    def on_finished(total: int) -> None:
        results["finished"].append(total)

    worker.item_started.connect(on_started)
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

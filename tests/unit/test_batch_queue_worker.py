"""BatchQueueWorkerThread curation wiring (Issue #60) and error routing (Issue #51)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread
from anki_miner.models.batch_queue import BatchQueue, QueueItem, QueueItemStatus
from anki_miner.models.processing import ProcessingResult
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.definition_service import DefinitionService


@pytest.fixture(autouse=True)
def _usable_offline_dictionary(monkeypatch: pytest.MonkeyPatch) -> None:
    def _verify_card_target(_service: AnkiService) -> None:
        return None

    def _has_usable_offline_provider(_service: DefinitionService) -> bool:
        return True

    monkeypatch.setattr(AnkiService, "verify_card_target", _verify_card_target)
    monkeypatch.setattr(DefinitionService, "has_usable_offline_provider", _has_usable_offline_provider)


def test_curation_attrs_use_item_offset_at_curator_time(tmp_path):
    """Season mode: the curator fires once per item, with the item's offset and
    first pair published on the worker while the bridge is parked."""
    captured = []

    pair = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")

    proc = MagicMock()

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
        curated = [] if curation_callback is None else curation_callback([_make_curation_word()])
        return ProcessingResult(
            total_words_found=1,
            new_words_found=len(curated or []),
            cards_created=len(curated or []),
        )

    proc.process_episode.side_effect = fake_process

    def cb(pool):
        captured.append(
            {
                "offset": worker._curation_offset,
                "video": worker._curation_video,
                "processor": worker.curation_processor,
                "pool": list(pool),
            }
        )
        return []

    item = QueueItem(
        video_folder=tmp_path / "video",
        subtitle_folder=tmp_path / "subs",
        display_name="Show",
        id="i1",
        subtitle_offset=3.0,
    )
    queue = MagicMock()
    queue.get_all_items.return_value = [item]

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

    assert captured, "curation callback was not invoked"
    assert len(captured) == 1
    assert captured[0]["offset"] == 3.0
    assert captured[0]["video"] == pair.video
    assert captured[0]["processor"] is proc
    assert [w.surface for w in captured[0]["pool"]] == ["食べる"]


def test_season_mode_passes_the_item_offset_on_every_call(tmp_path):
    """Season mode drives the shared processor twice per pair (pre-pass, then
    mine); both calls carry the item's own offset."""
    pair = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")

    proc = MagicMock()
    offsets: list = []

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
        offsets.append(kwargs.get("subtitle_offset"))
        curated = [] if curation_callback is None else curation_callback([_make_curation_word()])
        return ProcessingResult(
            total_words_found=1,
            new_words_found=len(curated or []),
            cards_created=len(curated or []),
        )

    proc.process_episode.side_effect = fake_process

    item = QueueItem(
        video_folder=tmp_path / "video",
        subtitle_folder=tmp_path / "subs",
        display_name="Show",
        id="i1",
        subtitle_offset=3.0,
    )
    queue = MagicMock()
    queue.get_all_items.return_value = [item]

    worker = BatchQueueWorkerThread(
        queue, AnkiMinerConfig(), MagicMock(), None, curation_callback=lambda pool: list(pool)
    )

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

    assert offsets == [3.0, 3.0], offsets


def _make_curation_word():
    from anki_miner.models.word import TokenizedWord

    return TokenizedWord(
        surface="食べる",
        lemma="食べる",
        reading="たべる",
        sentence="文",
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
    )


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

    def on_failed(item_id: str, msg: str, _cards: int) -> None:
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
    failed_with_counts: list[tuple] = []
    worker.item_failed.connect(lambda *args: failed_with_counts.append(args))

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
    assert failed_with_counts == [(queue.get_all_items()[0].id, msg, 3)]
    # Partial cards still count toward queue total
    assert results["finished"] == [3], "queue_finished should include cards from successful pairs"


def test_partial_series_retry_emits_only_new_cards_after_cumulative_row_total(tmp_path):
    """Retry keeps lifetime row count while signals report only current-run cards."""
    pair1 = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))
    pair2 = SimpleNamespace(video=Path("/tmp/ep2.mkv"), subtitle=Path("/tmp/ep2.ass"))
    queue = BatchQueue()
    item = queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")

    first_processor = MagicMock()
    first_processor.process_episode.side_effect = [_ok_result(cards=3), _failed_result()]
    first_worker = _make_worker_with_queue(queue)
    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=first_processor,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair1, pair2],
        ),
    ):
        first_worker.run()

    assert item.status == QueueItemStatus.ERROR
    assert item.cards_created == 3
    assert queue.reset_failed_for_retry() == 1

    retry_processor = MagicMock()
    retry_processor.process_episode.return_value = _ok_result(cards=2)
    retry_worker = _make_worker_with_queue(queue)
    retry_results = _wire_status_slots(retry_worker, queue)
    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=retry_processor,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair1, pair2],
        ),
    ):
        retry_worker.run()

    processed_videos = [call.args[0] for call in retry_processor.process_episode.call_args_list]
    assert processed_videos == [pair2.video]
    assert item.cards_created == 5
    assert retry_results["completed"] == [(item.id, 2)]
    assert retry_results["finished"] == [2]


def test_retry_skips_only_committed_pair_path_when_episode_numbers_match(tmp_path):
    pair1 = SimpleNamespace(
        video=tmp_path / "release-a" / "ep1.mkv",
        subtitle=tmp_path / "release-a" / "ep1.ass",
    )
    pair2 = SimpleNamespace(
        video=tmp_path / "release-b" / "ep1.mkv",
        subtitle=tmp_path / "release-b" / "ep1.ass",
    )
    queue = BatchQueue()
    item = queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")
    item.cards_created = 3
    item.committed_pair_keys = {(pair1.video.resolve(), pair1.subtitle.resolve())}

    processor = MagicMock()
    processor.process_episode.return_value = _ok_result(cards=2)
    worker = _make_worker_with_queue(queue)
    results = _wire_status_slots(worker, queue)
    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=processor,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair1, pair2],
        ),
    ):
        worker.run()

    processed_videos = [call.args[0] for call in processor.process_episode.call_args_list]
    assert processed_videos == [pair2.video]
    assert item.cards_created == 5
    assert results["completed"] == [(item.id, 2)]
    assert results["finished"] == [2]


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
    worker.item_failed.connect(lambda item_id, msg, _cards: results["failed"].append((item_id, msg)))
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


def test_cancel_during_final_pair_returns_item_to_pending(tmp_path):
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))
    queue = BatchQueue()
    item = queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")
    proc = MagicMock()

    def cancel_during_pair(*_args, **_kwargs):
        worker.cancel()
        return _ok_result(cards=2)

    proc.process_episode.side_effect = cancel_during_pair
    worker = _make_worker_with_queue(queue)
    results = _wire_capture_only(worker)
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

    assert item.status == QueueItemStatus.PENDING
    assert item.cards_created == 2
    assert item.committed_pair_keys == {(pair.video.resolve(), pair.subtitle.resolve())}
    assert results["completed"] == []
    assert results["failed"] == []
    assert results["finished"] == [2]


def test_zero_commit_cancel_during_final_pair_returns_item_to_pending(tmp_path):
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))
    queue = BatchQueue()
    item = queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")
    proc = MagicMock()

    def cancel_during_pair(*_args, **_kwargs):
        worker.cancel()
        return _failed_result()

    proc.process_episode.side_effect = cancel_during_pair
    worker = _make_worker_with_queue(queue)
    results = _wire_capture_only(worker)
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

    assert item.status == QueueItemStatus.PENDING
    assert item.cards_created == 0
    assert item.committed_pair_keys == set()
    assert results["completed"] == []
    assert results["failed"] == []
    assert results["finished"] == [0]


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
    queue.get_all_items.return_value = [
        QueueItem(video_folder=Path("v"), subtitle_folder=Path("s"), display_name=f"S{i}", id=f"i{i}") for i in range(3)
    ]

    worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock())
    results = _wire_capture_only(worker)
    started_totals: list[int] = []
    worker.queue_started.connect(started_totals.append)

    worker.cancel()
    worker.run()

    assert started_totals == [3]
    assert results["started"] == []
    assert results["completed"] == []
    assert results["failed"] == []
    assert results["finished"] == [0]


def test_setup_error_emits_item_failed(tmp_path):
    """process_episode raising SetupError causes item_failed to be emitted for that item."""
    pair = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")

    proc = MagicMock()
    proc.process_episode.side_effect = SetupError("note type not found")

    item = QueueItem(
        video_folder=tmp_path / "video",
        subtitle_folder=tmp_path / "subs",
        display_name="Show",
        id="i1",
    )
    queue = MagicMock()
    queue.get_all_items.return_value = [item]

    config = AnkiMinerConfig()
    worker = BatchQueueWorkerThread(queue, config, MagicMock(), None)

    failed_emissions = []
    worker.item_failed.connect(lambda item_id, msg, _cards: failed_emissions.append((item_id, msg)))

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
# One processor for the whole queue, closed at the run's end (Windows freeze fix)
# ---------------------------------------------------------------------------


def test_run_builds_one_processor_and_closes_it_once(tmp_path):
    """A 2-item queue builds ONE processor and closes it after the last item.

    Only ``subtitle_offset`` differed per item, and that is a per-call argument
    now, so nothing is left to rebuild between items. The run-end close is still
    the Windows back-to-back-mining handle release.
    """
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")
    queue.add_item(tmp_path / "video2", tmp_path / "subs2", "Show2")

    proc = MagicMock(name="proc")

    order: list[str] = []
    proc.close.side_effect = lambda: order.append("close")

    def _process(*_args, **_kwargs):
        order.append("process")
        return _ok_result(cards=1)

    proc.process_episode.side_effect = _process

    def _build(*_args, **_kwargs):
        order.append("build")
        return proc

    worker = _make_worker_with_queue(queue)
    results = _wire_status_slots(worker, queue)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            side_effect=_build,
        ) as create_ep,
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    create_ep.assert_called_once()
    proc.close.assert_called_once_with()
    assert order == ["build", "process", "process", "close"], order
    assert results["finished"] == [2], results["finished"]


def test_one_subtitle_parser_service_for_the_whole_queue(tmp_path):
    """Three queue items, ONE SubtitleParserService.

    The per-item processor rebuild constructed a fresh parser per item — a new
    fugashi Tagger plus the loss of every cross-parse dictionary memo — purely
    because the item's offset was baked into a config copy.
    """
    import dataclasses

    from anki_miner.gui.utils import service_factory
    from anki_miner.orchestration.episode_processor import EpisodeProcessor

    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    for index in range(3):
        queue.add_item(tmp_path / f"video{index}", tmp_path / f"subs{index}", f"Show{index}", float(index))

    # Every on-disk path under tmp_path: the real service factory runs here.
    config = dataclasses.replace(
        AnkiMinerConfig(),
        dicts_root=tmp_path / "dicts",
        known_words_db_path=tmp_path / "known_words.db",
        stats_db_path=tmp_path / "stats.db",
        media_temp_folder=tmp_path / "media",
    )

    real_parser_cls = service_factory.SubtitleParserService
    parsers: list = []

    def _spy_parser(*args, **kwargs):
        parser = real_parser_cls(*args, **kwargs)
        parsers.append(parser)
        return parser

    offsets: list = []

    def _fake_process(_self, *_args, **kwargs):
        offsets.append(kwargs.get("subtitle_offset"))
        return _ok_result(cards=1)

    worker = BatchQueueWorkerThread(queue, config, MagicMock())
    _wire_status_slots(worker, queue)

    with (
        patch.object(service_factory, "SubtitleParserService", side_effect=_spy_parser),
        patch.object(EpisodeProcessor, "process_episode", autospec=True, side_effect=_fake_process),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    assert len(parsers) == 1, f"expected one parser for the run, built {len(parsers)}"
    # Each item still mines on its own offset — passed per call, not per config.
    assert offsets == [0.0, 1.0, 2.0]


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
    """A processor.close() that raises must not lose the run's result."""
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")
    queue.add_item(tmp_path / "video2", tmp_path / "subs2", "Show2")

    proc = MagicMock(name="proc")
    proc.process_episode.side_effect = [_ok_result(cards=2), _ok_result(cards=3)]
    proc.close.side_effect = RuntimeError("close boom")

    worker = _make_worker_with_queue(queue)
    results = _wire_status_slots(worker, queue)

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

    # Both items processed and the total still reaches queue_finished.
    assert results["finished"] == [5], results["finished"]


# ---------------------------------------------------------------------------
# Shared AnkiService across batch items (OVH-011/013)
# ---------------------------------------------------------------------------


def test_shared_anki_service_passed_to_the_run_processor(tmp_path):
    """A single AnkiService instance must be built once and passed via
    anki_service= to the run's create_episode_processor call, so the vocab
    cache survives across all queue items."""
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

    # One processor for the whole run (2 items), over one shared AnkiService.
    assert len(captured_anki_services) == 1
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
    queue.get_all_items.return_value = []
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
        "anki_miner.gui.workers.batch_queue_worker.stale_resource_reimport_error",
        return_value="Dictionary 'X' needs reimport (schema upgrade) — Settings → Dictionaries → Reimport All",
    ):
        worker.run()

    assert len(errors) == 1
    assert "Reimport All" in errors[0]
    # Abort-once: no per-item rows at all.
    assert item_started == [] and item_completed == [] and item_failed == []
    assert finished == [0]  # queue_finished(total_cards=0)


def test_missing_offline_dictionary_aborts_queue_once(qapp):
    queue = MagicMock()
    queue.total_cards_created = 0
    queue.get_all_items.return_value = []
    worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock(), None)
    bundle = _bundle_mock()
    preflight_order: list[str] = []

    def _verify_card_target(_service: AnkiService) -> None:
        preflight_order.append("card-target")

    def _has_usable_offline_provider() -> bool:
        preflight_order.append("offline-dictionary")
        return False

    bundle.definition_service.has_usable_offline_provider.side_effect = _has_usable_offline_provider
    errors: list[str] = []
    started: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    finished: list[int] = []
    worker.error.connect(errors.append)

    def _record_started(item_id: str, display_name: str) -> None:
        started.append((item_id, display_name))

    def _record_failed(item_id: str, message: str, _cards: int) -> None:
        failed.append((item_id, message))

    worker.item_started.connect(_record_started)
    worker.item_failed.connect(_record_failed)
    worker.queue_finished.connect(finished.append)

    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_shared_lookup_services",
            return_value=bundle,
        ),
        patch.object(AnkiService, "verify_card_target", autospec=True, side_effect=_verify_card_target),
    ):
        worker.run()

    assert len(errors) == 1
    assert preflight_order == ["card-target", "offline-dictionary"]
    assert "Tools → Download Recommended Resources" in errors[0]
    assert "Settings → Dictionaries" in errors[0]
    assert started == []
    assert failed == []
    assert finished == [0]
    bundle.definition_service.has_usable_offline_provider.assert_called_once_with()
    bundle.close.assert_called_once_with()


# ---------------------------------------------------------------------------
# G1: a setup failure OUTSIDE the per-item try must not abort the run() thread.
# Code before the item loop (stale-dict gate, AnkiService construction,
# the run snapshot) runs OUTSIDE the per-item ``try/except``; run() itself was
# ``try/finally`` with NO ``except``. An exception there (e.g. AnkiService
# raising ValueError on missing anki_fields) propagated straight out of the
# reimplemented QThread.run() → PyQt6 FATAL abort. run() must instead catch it,
# emit ``error`` then ``queue_finished``, and return normally.
# ---------------------------------------------------------------------------


def test_setup_failure_emits_error_and_queue_finished(qapp):
    """AnkiService construction raising is caught: error + queue_finished, no propagation."""
    queue = MagicMock()
    queue.get_all_items.return_value = []
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


# ---------------------------------------------------------------------------
# Shared lookup services across batch items (dict/pitch/frequency rebuild fix)
# ---------------------------------------------------------------------------


def _bundle_mock():
    bundle = MagicMock(name="shared_lookup")
    bundle.load_result.info = ["Frequency data loaded: 1 source(s), 3 entries"]
    bundle.load_result.warnings = ["some warning"]
    return bundle


def test_shared_lookup_services_passed_to_the_run_processor(tmp_path):
    """One SharedLookupServices bundle per run: built once, passed via
    shared_lookup= to the run's create_episode_processor call."""
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
    assert captured == [bundle]


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


def test_close_failure_does_not_emit_second_summary(qtbot, tmp_path):
    pair = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))

    queue = BatchQueue()
    queue.add_item(tmp_path / "video1", tmp_path / "subs1", "Show1")

    proc = MagicMock()
    proc.process_episode.return_value = _ok_result(cards=4)
    bundle = _bundle_mock()
    bundle.close.side_effect = RuntimeError("close boom")

    worker = _make_worker_with_queue(queue)
    results = _wire_status_slots(worker, queue)
    errors: list[str] = []
    worker.error.connect(errors.append)

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
        qtbot.waitSignal(worker.finished, timeout=5000),
    ):
        worker.start()

    assert worker.wait(5000)
    assert errors == ["close boom"]
    assert results["finished"] == [4]


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


# ---------------------------------------------------------------------------
# The run is frozen, and it pauses only between series (D29-A)
# ---------------------------------------------------------------------------


def test_run_uses_the_supplied_order_and_ignores_later_queue_edits(tmp_path):
    """The snapshot is the run. Editing the panel mid-run changes nothing.

    Batch already ran from a snapshot while the visible cards stayed editable,
    so removing a row did not stop it creating that series' cards. Now the list
    is locked instead, and the worker is handed exactly what it will mine.
    """
    pair = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")
    proc = MagicMock()
    proc.process_episode.return_value = _ok_result(cards=1)

    queue = BatchQueue()
    first = queue.add_item(tmp_path / "v1", tmp_path / "s1", "B")
    second = queue.add_item(tmp_path / "v2", tmp_path / "s2", "A")

    worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock(), None, items=[second, first])
    results = _wire_capture_only(worker)

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
        # A row added after the snapshot must not join the run.
        queue.add_item(tmp_path / "v3", tmp_path / "s3", "C")
        worker.run()

    assert results["started"] == [second.id, first.id]


def test_pause_lands_between_series_not_inside_one(tmp_path):
    """Requested during series one, consumed before series two is picked."""
    pair = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")
    proc = MagicMock()

    queue = BatchQueue()
    first = queue.add_item(tmp_path / "v1", tmp_path / "s1", "One")
    second = queue.add_item(tmp_path / "v2", tmp_path / "s2", "Two")

    worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock(), None, items=[first, second])
    results = _wire_capture_only(worker)
    paused: list[int] = []
    # The pause parks the run; ending it from here is what lets the test finish
    # without a second thread, and proves the gate is reachable from outside.
    worker.run_paused.connect(lambda: (paused.append(1), worker.request_stop_after_current()))

    def _process(*_args, **_kwargs):
        # Asked for mid-episode; it must not take effect until the boundary.
        worker.request_pause_after_current()
        return _ok_result(cards=1)

    proc.process_episode.side_effect = _process

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

    # Series one ran to a real terminal result; series two never started.
    assert paused == [1]
    assert results["started"] == [first.id]
    assert results["completed"] == [(first.id, 1)]
    assert second.status is QueueItemStatus.PENDING
    assert len(results["finished"]) == 1


def test_finish_current_in_post_boundary_gap_leaves_next_series_pending(tmp_path):
    """A stop that lands after the boundary check must beat the next claim."""
    pair = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")
    proc = MagicMock()
    proc.process_episode.return_value = _ok_result(cards=1)

    queue = BatchQueue()
    first = queue.add_item(tmp_path / "v1", tmp_path / "s1", "One")
    second = queue.add_item(tmp_path / "v2", tmp_path / "s2", "Two")
    worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock(), None, items=[first, second])
    results = _wire_capture_only(worker)

    # The stop lands after the second item clears the boundary check but before
    # it claims the item — the only gap between the two.
    real_wait = worker._wait_at_boundary
    waits = 0

    def _wait_then_stop_in_gap() -> bool:
        nonlocal waits
        waits += 1
        cleared = real_wait()
        if waits == 2:
            worker.request_stop_after_current()
        return cleared

    worker._wait_at_boundary = _wait_then_stop_in_gap  # type: ignore[method-assign]

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

    assert results["started"] == [first.id]
    assert first.status is QueueItemStatus.COMPLETED
    assert second.status is QueueItemStatus.PENDING


def test_cancel_releases_a_worker_waiting_at_a_series_boundary():
    """A closed gate must never outlive a Cancel — that is a shutdown deadlock."""
    worker = BatchQueueWorkerThread(BatchQueue(), AnkiMinerConfig(), MagicMock(), None)
    worker.request_pause_after_current()
    worker._pause_requested.clear()
    worker._resume_gate.clear()

    worker.cancel()

    assert worker._resume_gate.is_set()


# ---------------------------------------------------------------------------
# Within-series episode progress (the queue bar's only within-item truth)
# ---------------------------------------------------------------------------


def test_item_pairs_progress_ticks_each_episode(tmp_path):
    """One (0, total) prime plus one tick per finished pair, addressed by item id."""
    pair1 = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))
    pair2 = SimpleNamespace(video=Path("/tmp/ep2.mkv"), subtitle=Path("/tmp/ep2.ass"))
    queue = BatchQueue()
    item = queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")

    proc = MagicMock()
    proc.process_episode.side_effect = [_ok_result(cards=3), _ok_result(cards=2)]

    worker = _make_worker_with_queue(queue)
    ticks: list[tuple] = []
    worker.item_pairs_progress.connect(lambda *args: ticks.append(args))

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

    assert ticks == [(item.id, 0, 2), (item.id, 1, 2), (item.id, 2, 2)]


def test_item_pairs_progress_counts_failed_attempts(tmp_path):
    """A pair that raises still concluded its attempt, so it still ticks."""
    pair1 = SimpleNamespace(video=Path("/tmp/ep1.mkv"), subtitle=Path("/tmp/ep1.ass"))
    pair2 = SimpleNamespace(video=Path("/tmp/ep2.mkv"), subtitle=Path("/tmp/ep2.ass"))
    queue = BatchQueue()
    item = queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")

    proc = MagicMock()
    proc.process_episode.side_effect = [_ok_result(cards=3), SetupError("Anki blipped")]

    worker = _make_worker_with_queue(queue)
    ticks: list[tuple] = []
    worker.item_pairs_progress.connect(lambda *args: ticks.append(args))

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

    assert ticks == [(item.id, 0, 2), (item.id, 1, 2), (item.id, 2, 2)]


# ---------------------------------------------------------------------------
# The PENDING-only gate a re-run depends on
# ---------------------------------------------------------------------------


def test_a_completed_item_handed_to_the_worker_is_skipped(tmp_path):
    """The worker's own gate: only PENDING runs.

    This is why QueuePanel.runnable_items resets a re-run row to PENDING before
    the run starts -- handing the worker a COMPLETED item mines nothing.
    """
    pair = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")
    queue = BatchQueue()
    item = queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")
    item.status = QueueItemStatus.COMPLETED

    processor = MagicMock()
    processor.process_episode.return_value = _ok_result(cards=2)
    worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock(), items=[item])
    results = _wire_status_slots(worker, queue)
    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=processor,
        ) as factory,
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair],
        ),
    ):
        worker.run()

    factory.assert_not_called()
    processor.process_episode.assert_not_called()
    assert item.status is QueueItemStatus.COMPLETED
    assert results["completed"] == []


def test_a_reset_item_re_mines_every_pair(tmp_path):
    """With the receipts cleared the worker sees every pair as pending again."""
    pair1 = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")
    pair2 = SimpleNamespace(video=tmp_path / "ep2.mkv", subtitle=tmp_path / "ep2.ass")
    queue = BatchQueue()
    item = queue.add_item(tmp_path / "video", tmp_path / "subs", "Show")
    item.status = QueueItemStatus.COMPLETED
    item.cards_created = 5
    item.committed_pair_keys = {
        (pair1.video.resolve(), pair1.subtitle.resolve()),
        (pair2.video.resolve(), pair2.subtitle.resolve()),
    }

    BatchQueue.reset_run_history(item)

    processor = MagicMock()
    processor.process_episode.return_value = _ok_result(cards=2)
    worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock(), items=[item])
    results = _wire_status_slots(worker, queue)
    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=processor,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[pair1, pair2],
        ),
    ):
        worker.run()

    processed_videos = [call.args[0] for call in processor.process_episode.call_args_list]
    assert processed_videos == [pair1.video, pair2.video]
    # The row's own count restarted at 0, so it reports only this run's cards.
    assert item.cards_created == 4
    assert results["completed"] == [(item.id, 4)]

"""Season-mode curation in BatchQueueWorkerThread: one curator per series item."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread
from anki_miner.models.batch_queue import BatchQueue, QueueItemStatus
from anki_miner.models.processing import ProcessingResult
from anki_miner.models.word import TokenizedWord
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.definition_service import DefinitionService

EP1 = Path("/tmp/ep1.mkv")
EP2 = Path("/tmp/ep2.mkv")
SUB1 = Path("/tmp/ep1.ass")
SUB2 = Path("/tmp/ep2.ass")


@pytest.fixture(autouse=True)
def _usable_offline_dictionary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AnkiService, "verify_card_target", lambda _self: None)
    monkeypatch.setattr(DefinitionService, "has_usable_offline_provider", lambda _self: True)


def _word(surface: str) -> TokenizedWord:
    return TokenizedWord(
        surface=surface,
        lemma=surface,
        reading="よみ",
        sentence="文",
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
        occurrence_count=1,
    )


def _pairs() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(video=EP1, subtitle=SUB1),
        SimpleNamespace(video=EP2, subtitle=SUB2),
    ]


def _make_processor(words_by_video: dict[Path, list[TokenizedWord]], stats=None):
    """Fake EpisodeProcessor: invokes the curation callback like production
    (pre-pass captures, mine pass consumes its return verbatim) and records
    one difficulty row per successful call."""
    proc = MagicMock()
    proc.stats_service = stats

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
        if proc.stats_service is not None:
            proc.stats_service.record_difficulty("Show", video.name, 10, 5)
        curated = list(words_by_video.get(video, []))
        if curation_callback is not None:
            curated = curation_callback(curated)
        if curated is None:
            return ProcessingResult(
                total_words_found=10,
                new_words_found=0,
                cards_created=0,
                errors=["Processing cancelled by user"],
            )
        return ProcessingResult(
            total_words_found=10,
            new_words_found=len(curated),
            cards_created=len(curated),
        )

    proc.process_episode.side_effect = fake_process
    return proc


def _run_worker(proc, curation_cb, pairs=None, tmp_path=None):
    queue = BatchQueue()
    queue.add_item(Path("/tmp/video"), Path("/tmp/subs"), "Show")
    item = queue.get_all_items()[0]
    worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock(), curation_callback=curation_cb)
    progress: list[tuple[int, int]] = []
    worker.item_pairs_progress.connect(lambda _id, done, total: progress.append((done, total)))
    with (
        patch(
            "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
            return_value=proc,
        ),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=pairs if pairs is not None else _pairs(),
        ),
    ):
        worker.run()
    return worker, item, progress


class TestSeasonFlow:
    def test_bridge_invoked_once_with_merged_pool(self):
        words = {EP1: [_word("猫"), _word("犬")], EP2: [_word("猫"), _word("鳥")]}
        proc = _make_processor(words)
        bridge = MagicMock(return_value=[])
        _run_worker(proc, bridge)
        assert bridge.call_count == 1
        pool = bridge.call_args.args[0]
        # 猫 deduped across episodes; 犬 and 鳥 unique.
        assert sorted(w.mined_form for w in pool) == ["犬", "猫", "鳥"]
        cat = next(w for w in pool if w.mined_form == "猫")
        assert cat.occurrence_count == 2
        assert cat.video_file == EP1

    def test_prepass_uses_capture_not_bridge(self):
        words = {EP1: [_word("猫")], EP2: [_word("犬")]}
        proc = _make_processor(words)
        bridge = MagicMock(return_value=[])
        _run_worker(proc, bridge)
        prepass_callbacks = [c.kwargs["curation_callback"] for c in proc.process_episode.call_args_list[:2]]
        for cb in prepass_callbacks:
            assert cb is not bridge
            assert getattr(cb, "suppress_curation_messages", False) is True

    def test_media_map_published_during_curator_and_cleared_after(self):
        words = {EP1: [_word("猫")], EP2: [_word("犬")]}
        proc = _make_processor(words)
        seen: dict = {}

        def bridge(pool):
            seen["map"] = dict(worker._curation_media_map)
            seen["video"] = worker._curation_video
            return []

        queue = BatchQueue()
        queue.add_item(Path("/tmp/video"), Path("/tmp/subs"), "Show")
        worker = BatchQueueWorkerThread(queue, AnkiMinerConfig(), MagicMock(), curation_callback=bridge)
        with (
            patch(
                "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
                return_value=proc,
            ),
            patch(
                "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
                return_value=_pairs(),
            ),
        ):
            worker.run()
        offset = queue.get_all_items()[0].subtitle_offset
        assert seen["map"] == {EP1: (SUB1, offset), EP2: (SUB2, offset)}
        assert seen["video"] == EP1
        assert worker._curation_media_map is None

    def test_mine_pass_only_owning_episodes_with_verbatim_objects(self):
        cat, dog = _word("猫"), _word("犬")
        words = {EP1: [cat], EP2: [dog]}
        proc = _make_processor(words)

        def bridge(pool):
            # User keeps only ep2's word.
            return [w for w in pool if w.mined_form == "犬"]

        _worker, item, _progress = _run_worker(proc, bridge)
        # 2 pre-pass calls + 1 mine call (ep1's subset is empty).
        assert proc.process_episode.call_count == 3
        mine_call = proc.process_episode.call_args_list[2]
        assert mine_call.args[0] == EP2
        mined = mine_call.kwargs["curation_callback"](["ignored-input"])
        assert len(mined) == 1
        assert mined[0].mined_form == "犬"
        assert mined[0].video_file == EP2
        # Both pairs committed: ep1 as empty-subset skip, ep2 as mined success.
        assert len(item.committed_pair_keys) == 2
        assert item.status == QueueItemStatus.COMPLETED

    def test_reject_none_leaves_item_pending_nothing_committed(self):
        words = {EP1: [_word("猫")], EP2: [_word("犬")]}
        proc = _make_processor(words)
        bridge = MagicMock(return_value=None)
        _worker, item, _progress = _run_worker(proc, bridge)
        assert item.committed_pair_keys == set()
        assert item.status == QueueItemStatus.PENDING
        assert item.cards_created == 0

    def test_confirm_empty_commits_all_completed_zero_cards(self):
        words = {EP1: [_word("猫")], EP2: [_word("犬")]}
        proc = _make_processor(words)
        bridge = MagicMock(return_value=[])
        _worker, item, progress = _run_worker(proc, bridge)
        assert len(item.committed_pair_keys) == 2
        assert item.status == QueueItemStatus.COMPLETED
        assert item.cards_created == 0
        assert progress[-1] == (2, 2)

    def test_prepass_failure_excluded_from_pool_item_error(self):
        words = {EP1: [_word("猫")], EP2: [_word("犬")]}
        proc = _make_processor(words)
        original = proc.process_episode.side_effect

        def failing_first(video, subtitle, **kwargs):
            if video == EP1:
                raise RuntimeError("boom")
            return original(video, subtitle, **kwargs)

        proc.process_episode.side_effect = failing_first
        bridge = MagicMock(return_value=[])
        _worker, item, _progress = _run_worker(proc, bridge)
        pool = bridge.call_args.args[0]
        assert [w.mined_form for w in pool] == ["犬"]
        assert item.status == QueueItemStatus.ERROR
        assert "1/2 episodes failed" in item.error_message
        # The failed pair is NOT committed; the confirmed-empty pair is.
        assert len(item.committed_pair_keys) == 1

    def test_progress_ticks_only_in_mine_pass(self):
        words = {EP1: [_word("猫")], EP2: [_word("犬")]}
        proc = _make_processor(words)

        progress_at_curator: list[tuple[int, int]] = []

        def bridge(pool):
            return list(pool)

        _worker, _item, progress = _run_worker(proc, bridge)
        del progress_at_curator
        # Initial (0, 2), then one tick per mined pair — nothing from pre-pass.
        assert progress == [(0, 2), (1, 2), (2, 2)]

    def test_record_difficulty_once_per_episode(self):
        stats = MagicMock()
        words = {EP1: [_word("猫")], EP2: [_word("犬")]}
        proc = _make_processor(words, stats=stats)
        bridge = MagicMock(side_effect=lambda pool: list(pool))
        _run_worker(proc, bridge)
        # Pre-pass records via the real stats mock; the mine pass swaps in
        # MinePassStats whose record_difficulty is a no-op.
        assert stats.record_difficulty.call_count == 2
        assert stats.record_session.call_count == 0

    def test_cancel_during_mine_pass_keeps_committed_pairs_item_pending(self):
        words = {EP1: [_word("猫")], EP2: [_word("犬")]}
        proc = _make_processor(words)
        original = proc.process_episode.side_effect
        state: dict = {}

        def cancelling_mine(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
            result = original(
                video,
                subtitle,
                progress_callback=progress_callback,
                curation_callback=curation_callback,
            )
            marked = getattr(curation_callback, "suppress_curation_messages", False)
            if not marked and curation_callback is not None:
                # First mine-pass call: cancel after it concludes.
                state["worker"].cancel()
            return result

        proc.process_episode.side_effect = cancelling_mine

        queue = BatchQueue()
        queue.add_item(Path("/tmp/video"), Path("/tmp/subs"), "Show")
        item = queue.get_all_items()[0]
        worker = BatchQueueWorkerThread(
            queue, AnkiMinerConfig(), MagicMock(), curation_callback=lambda pool: list(pool)
        )
        state["worker"] = worker
        with (
            patch(
                "anki_miner.gui.workers.batch_queue_worker.create_episode_processor",
                return_value=proc,
            ),
            patch(
                "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
                return_value=_pairs(),
            ),
        ):
            worker.run()
        # First mined pair stays committed; second never ran; item PENDING.
        assert len(item.committed_pair_keys) == 1
        assert item.status == QueueItemStatus.PENDING

    def test_no_reviewable_words_skips_curator_and_completes(self):
        proc = _make_processor({EP1: [], EP2: []})
        bridge = MagicMock()
        _worker, item, progress = _run_worker(proc, bridge)
        bridge.assert_not_called()
        assert item.status == QueueItemStatus.COMPLETED
        assert len(item.committed_pair_keys) == 2
        assert progress[-1] == (2, 2)

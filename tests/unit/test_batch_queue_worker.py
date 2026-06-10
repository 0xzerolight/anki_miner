"""BatchQueueWorkerThread curation wiring (Issue #60)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread


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

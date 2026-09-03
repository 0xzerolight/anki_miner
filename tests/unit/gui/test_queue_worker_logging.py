"""Queue-worker diagnostic receipts: start, refusals, per-item, end.

The Backfill-shaped support report is the motivation: three whole-queue
refusals (stale reimport, ASR preflight, queue preflight) abort a run before
any item is mined and left *nothing* in the log — the user saw a dialog, the
log saw silence. These tests pin the receipts that make such a run
reconstructable from ``anki_miner.log`` alone: one start line naming the queue
shape, one WARNING per refusal carrying its reason and message, one line per
finished item, and one end line with the elapsed time and the tallies.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.workers.audiobook_queue_worker import AudiobookQueueWorker
from anki_miner.models.audiobook_queue import AudiobookQueueItem
from anki_miner.models.processing import ProcessingResult

_WORKER_LOGGER = "anki_miner.gui.workers.audiobook_queue_worker"


def _make_item(stem: str = "book01") -> AudiobookQueueItem:
    return AudiobookQueueItem(
        audio_file=Path(f"/audio/{stem}.mp3"),
        subtitle_file=Path(f"/audio/{stem}.srt"),
    )


def _make_worker(config, items, *, result=None, curation_callback=None):
    processor = MagicMock()
    processor.process_episode = MagicMock(return_value=result if result is not None else ProcessingResult(1, 1, 3))
    return AudiobookQueueWorker(
        processor=processor,
        config=config,
        items=items,
        curation_callback=curation_callback,
    )


def _lines(caplog, prefix: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


# ---------------------------------------------------------------------------
# Start receipt
# ---------------------------------------------------------------------------


def test_run_logs_queue_shape_at_start(qapp, test_config, caplog):
    """The start line names the queue size, its head, and how it was built."""
    worker = _make_worker(test_config, [_make_item("book01"), _make_item("book02")])

    with caplog.at_level(logging.INFO, logger=_WORKER_LOGGER):
        worker.run()

    started = _lines(caplog, "AudiobookQueueWorker started:")
    assert len(started) == 1, caplog.text
    assert "items=2" in started[0]
    assert "book01.mp3" in started[0]
    assert "curation=False" in started[0]
    assert "processor_factory=False" in started[0]


# ---------------------------------------------------------------------------
# Whole-queue refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hook", "reason"),
    [
        ("_stale_reimport_message", "stale_reimport"),
        ("_asr_preflight_message", "asr_preflight"),
    ],
)
def test_refusal_before_any_item_is_logged(qapp, test_config, caplog, hook, reason):
    """A pre-loop refusal leaves a WARNING naming its reason and message."""
    worker = _make_worker(test_config, [_make_item()])
    setattr(worker, hook, lambda: "Reimport your dictionaries first.")

    with caplog.at_level(logging.INFO, logger=_WORKER_LOGGER):
        worker.run()

    refused = _lines(caplog, "Queue refused:")
    assert len(refused) == 1, caplog.text
    assert "worker=AudiobookQueueWorker" in refused[0]
    assert f"reason={reason}" in refused[0]
    assert "Reimport your dictionaries first." in refused[0]
    assert [r.levelno for r in caplog.records if r.getMessage().startswith("Queue refused:")] == [logging.WARNING]


def test_queue_preflight_refusal_is_logged(qapp, test_config, caplog):
    """The setup-check refusal is logged with its own reason token."""
    from anki_miner.exceptions import SetupError

    worker = _make_worker(test_config, [_make_item()])
    worker._processor._preflight_card_target.side_effect = SetupError("Deck missing")

    with caplog.at_level(logging.INFO, logger=_WORKER_LOGGER):
        worker.run()

    refused = _lines(caplog, "Queue refused:")
    assert len(refused) == 1, caplog.text
    assert "reason=queue_preflight" in refused[0]
    assert "Deck missing" in refused[0]


def test_refused_run_still_logs_an_end_receipt(qapp, test_config, caplog):
    """Even a run that mined nothing closes its own start line."""
    worker = _make_worker(test_config, [_make_item()])
    worker._stale_reimport_message = lambda: "Reimport"

    with caplog.at_level(logging.INFO, logger=_WORKER_LOGGER):
        worker.run()

    ended = _lines(caplog, "AudiobookQueueWorker finished:")
    assert len(ended) == 1, caplog.text
    assert "elapsed_s=" in ended[0]


# ---------------------------------------------------------------------------
# Per-item receipts and the end tally
# ---------------------------------------------------------------------------


def test_per_item_outcomes_and_end_tally(qapp, test_config, caplog):
    """Every finished item logs once; the end line tallies the outcomes."""
    items = [_make_item("book01"), _make_item("book02")]
    worker = _make_worker(test_config, items)
    outcomes = [ProcessingResult(9, 4, 7), RuntimeError("disk full")]

    def _process(*_args, **_kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    worker._processor.process_episode.side_effect = _process

    with caplog.at_level(logging.INFO, logger=_WORKER_LOGGER):
        worker.run()

    item_lines = _lines(caplog, "Queue item:")
    assert len(item_lines) == 2, caplog.text
    assert "worker=AudiobookQueueWorker" in item_lines[0]
    assert "idx=0" in item_lines[0]
    assert "book01.mp3" in item_lines[0]
    assert "outcome=success" in item_lines[0]
    assert "attempts=1" in item_lines[0]
    assert "cards=7" in item_lines[0]
    assert "error=-" in item_lines[0]

    assert "idx=1" in item_lines[1]
    assert "outcome=failed" in item_lines[1]
    assert "disk full" in item_lines[1]
    failed_levels = [
        r.levelno for r in caplog.records if r.getMessage().startswith("Queue item:") and "idx=1" in r.getMessage()
    ]
    assert failed_levels == [logging.WARNING]

    ended = _lines(caplog, "AudiobookQueueWorker finished:")
    assert len(ended) == 1, caplog.text
    assert "succeeded=1" in ended[0]
    assert "failed=1" in ended[0]
    assert "cancelled=0" in ended[0]


def test_cancelled_item_counts_as_cancelled_not_failed(qapp, test_config, caplog):
    """A Stop mid-mine is tallied apart from a genuine failure."""
    from anki_miner.models.processing import CANCELLED_ERROR

    worker = _make_worker(test_config, [_make_item()], result=ProcessingResult(0, 0, 0, [CANCELLED_ERROR]))

    with caplog.at_level(logging.INFO, logger=_WORKER_LOGGER):
        worker.run()

    item_lines = _lines(caplog, "Queue item:")
    assert len(item_lines) == 1, caplog.text
    assert "outcome=cancelled" in item_lines[0]
    ended = _lines(caplog, "AudiobookQueueWorker finished:")
    assert "cancelled=1" in ended[0]
    assert "failed=0" in ended[0]


# ---------------------------------------------------------------------------
# Failure receipts keep the queue position
# ---------------------------------------------------------------------------


def test_processor_build_failure_names_the_resource_shape(qapp, test_config, caplog):
    """A factory blow-up records the language and dictionary chain it used."""

    def _boom():
        raise RuntimeError("registry exploded")

    worker = AudiobookQueueWorker(
        processor=None,
        config=test_config,
        items=[_make_item()],
        curation_callback=None,
        processor_factory=_boom,
    )

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.workers._queue_worker_base"):
        worker.run()

    failed = [r.getMessage() for r in caplog.records if "processor build failed" in r.getMessage()]
    assert len(failed) == 1, caplog.text
    assert f"language={test_config.language}" in failed[0]
    assert f"chain={len(test_config.dictionary_chain)}" in failed[0]
    assert str(test_config.dicts_root) in failed[0]


def test_run_failure_names_the_item_it_died_on(qapp, test_config, caplog):
    """The QThread boundary catch-all carries the queue position."""
    worker = _make_worker(test_config, [_make_item(), _make_item("book02")])
    worker._run_item = MagicMock(side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.workers._queue_worker_base"):
        worker.run()

    failed = [r.getMessage() for r in caplog.records if "run failed" in r.getMessage()]
    assert len(failed) == 1, caplog.text
    assert "idx=0" in failed[0]
    assert "items=2" in failed[0]


def test_retry_verdicts_are_logged_at_debug(qapp, test_config, caplog):
    """Each attempt records whether repeating it was permitted."""
    worker = _make_worker(test_config, [_make_item()])
    root = logging.getLogger()
    am = logging.getLogger("anki_miner")
    before = (root.level, am.level)
    try:
        with caplog.at_level(logging.DEBUG, logger=_WORKER_LOGGER):
            worker.run()
    finally:
        root.setLevel(before[0])
        am.setLevel(before[1])

    retries = _lines(caplog, "Queue retry:")
    assert len(retries) == 1, caplog.text
    assert "idx=0" in retries[0]
    assert "attempt=1" in retries[0]
    assert "retryable=" in retries[0]
    assert "abort=False" in retries[0]


# ---------------------------------------------------------------------------
# YouTube: the two receipts that used to be silent or contextless
# ---------------------------------------------------------------------------


def _make_youtube_worker(config, tmp_path):
    from dataclasses import replace

    from anki_miner.gui.workers.youtube_queue_worker import YouTubeQueueWorker
    from anki_miner.models.youtube import VideoInfo
    from anki_miner.models.youtube_queue import YouTubeItemStatus, YouTubeQueueItem

    item = YouTubeQueueItem(
        url="https://www.youtube.com/watch?v=abc",
        status=YouTubeItemStatus.READY,
        video_id="abc",
        resolved_sub_mode="manual_only",  # type: ignore[arg-type]
        video_info=VideoInfo(
            video_id="abc",
            title="Some Title",
            duration_s=120,
            has_manual_ja_subs=True,
            has_auto_ja_subs=False,
            is_live=False,
            is_age_restricted=False,
        ),
    )
    processor = MagicMock()
    return YouTubeQueueWorker(
        processor=processor,
        config=replace(config, media_temp_folder=tmp_path / "temp_media"),
        items=[item],
        curation_callback=None,
    )


def test_youtube_mid_download_cancel_is_not_silent(qapp, test_config, tmp_path, caplog):
    """Abandoning the queue mid-download logs why, with the item it died on."""
    from anki_miner.exceptions.youtube import YouTubeFetchError

    worker = _make_youtube_worker(test_config, tmp_path)

    def _cancel_mid_fetch(*_args, **_kwargs):
        # The real shape: Stop lands while yt-dlp is running, the fetcher kills
        # the process tree and raises. Cancelling before run() would instead be
        # caught by the loop-top check, before any item is claimed.
        worker.cancel()
        raise YouTubeFetchError("Cancelled")

    worker._processor.process_youtube_url.side_effect = _cancel_mid_fetch

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.workers.youtube_queue_worker"):
        worker.run()

    lines = _lines(caplog, "YouTube fetch cancelled mid-download:")
    assert len(lines) == 1, caplog.text
    assert "idx=0" in lines[0]
    assert "v=abc" in lines[0]
    levels = [r.levelno for r in caplog.records if r.getMessage().startswith("YouTube fetch cancelled mid-download:")]
    assert levels == [logging.WARNING]


def test_youtube_item_failure_names_the_url(qapp, test_config, tmp_path, caplog):
    """The per-item traceback carries the index and URL that produced it."""
    worker = _make_youtube_worker(test_config, tmp_path)
    worker._processor.process_youtube_url.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.workers.youtube_queue_worker"):
        worker.run()

    failed = [r.getMessage() for r in caplog.records if "item failed" in r.getMessage()]
    assert len(failed) == 1, caplog.text
    assert "idx=0" in failed[0]
    assert "v=abc" in failed[0]

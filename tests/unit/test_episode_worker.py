"""Tests for EpisodeWorkerThread audio_track_override forwarding."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.workers.episode_worker import EpisodeWorkerThread


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_worker(qapp, audio_track_override=..., **kwargs):
    """Helper to build an EpisodeWorkerThread with sensible defaults."""
    processor = MagicMock(name="EpisodeProcessor")
    processor.process_episode.return_value = MagicMock(name="ProcessingResult")

    common = {
        "processor": processor,
        "video_file": Path("/fake/video.mkv"),
        "subtitle_file": Path("/fake/subs.ass"),
        "preview_mode": False,
        "progress_callback": MagicMock(name="ProgressCallback"),
    }
    common.update(kwargs)

    if audio_track_override is ...:
        worker = EpisodeWorkerThread(**common)
    else:
        worker = EpisodeWorkerThread(**common, audio_track_override=audio_track_override)

    return worker


def test_worker_forwards_override_to_process_episode(qapp):
    """Worker passes audio_track_override to process_episode when set."""
    worker = _make_worker(qapp, audio_track_override=2)
    worker.run()
    worker.processor.process_episode.assert_called_once()
    _, kwargs = worker.processor.process_episode.call_args
    assert kwargs.get("audio_track_override") == 2


def test_worker_default_override_is_none(qapp):
    """Worker passes audio_track_override=None when not specified."""
    worker = _make_worker(qapp)
    worker.run()
    worker.processor.process_episode.assert_called_once()
    _, kwargs = worker.processor.process_episode.call_args
    assert kwargs.get("audio_track_override") is None


def test_worker_explicit_none_override(qapp):
    """Worker passes audio_track_override=None when explicitly set to None."""
    worker = _make_worker(qapp, audio_track_override=None)
    worker.run()
    _, kwargs = worker.processor.process_episode.call_args
    assert kwargs.get("audio_track_override") is None


def test_worker_stores_override_attribute(qapp):
    """audio_track_override is stored as an instance attribute."""
    worker = _make_worker(qapp, audio_track_override=3)
    assert worker.audio_track_override == 3


def test_worker_default_attribute_is_none(qapp):
    """audio_track_override attribute defaults to None."""
    worker = _make_worker(qapp)
    assert worker.audio_track_override is None


def test_curation_processor_exposes_constructor_processor(qapp):
    """Typed curation_processor contract (T-60): returns the run's processor."""
    worker = _make_worker(qapp)
    assert worker.curation_processor is worker.processor


# ---------------------------------------------------------------------------
# Cancel propagation / error-emit / result-suppression (run() called directly)
# ---------------------------------------------------------------------------


def test_cancel_propagates_to_processor(qapp):
    """cancel() sets the worker flag AND forwards to processor.cancel()."""
    worker = _make_worker(qapp)

    worker.cancel()

    assert worker.is_cancelled is True
    worker.processor.cancel.assert_called_once_with()


def test_cancel_before_run_skips_process_episode_and_emit(qapp):
    """A pre-run cancel returns immediately: no processing, no result_ready."""
    worker = _make_worker(qapp)
    results: list = []
    worker.result_ready.connect(results.append)

    worker.cancel()
    worker.run()

    worker.processor.process_episode.assert_not_called()
    assert results == []


def test_result_suppressed_when_cancelled_after_processing(qapp):
    """A cancel that lands while process_episode is mid-flight suppresses the
    result_ready emit (the post-call check_cancelled guard)."""
    worker = _make_worker(qapp)

    def _cancel_mid_run(*args, **kwargs):
        worker.cancel()  # user pressed Cancel during the pipeline
        return MagicMock(name="ProcessingResult")

    worker.processor.process_episode.side_effect = _cancel_mid_run

    results: list = []
    worker.result_ready.connect(results.append)
    worker.run()

    # Processing ran, but the late cancel swallows the result.
    worker.processor.process_episode.assert_called_once()
    assert results == []


def test_error_emitted_with_prefix_when_process_episode_raises(qapp):
    """A processing exception surfaces on the error signal, not result_ready."""
    worker = _make_worker(qapp)
    worker.processor.process_episode.side_effect = RuntimeError("disk full")

    errors: list[str] = []
    results: list = []
    worker.error.connect(errors.append)
    worker.result_ready.connect(results.append)

    worker.run()

    assert results == []
    assert len(errors) == 1
    assert errors[0] == "Error processing episode: disk full"


def test_error_suppressed_when_cancelled_during_failure(qapp):
    """A raise that coincides with a cancel stays silent (cancelled runs emit
    nothing — neither result nor error)."""
    worker = _make_worker(qapp)

    def _cancel_then_raise(*args, **kwargs):
        worker.cancel()
        raise RuntimeError("boom")

    worker.processor.process_episode.side_effect = _cancel_then_raise

    errors: list[str] = []
    worker.error.connect(errors.append)

    worker.run()

    assert errors == []


def test_successful_run_emits_result_ready(qapp):
    """The happy path forwards the processor's ProcessingResult verbatim."""
    worker = _make_worker(qapp)
    sentinel = MagicMock(name="ProcessingResult")
    worker.processor.process_episode.return_value = sentinel

    results: list = []
    worker.result_ready.connect(results.append)
    worker.run()

    assert results == [sentinel]

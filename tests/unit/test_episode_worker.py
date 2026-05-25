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

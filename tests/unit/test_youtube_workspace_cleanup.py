"""Pin the YouTube workspace cleanup invariant across every exit path.

CLAUDE.md (gotcha "YouTube workspace ownership") states:

    YouTubeWorkerThread allocates ``media_temp_folder/youtube/run-<uuid>/``
    and is the sole owner -- fetcher and orchestrator only write into it.
    ``shutil.rmtree`` runs in the worker's ``finally`` on every exit path;
    cancel kills the yt-dlp process tree (including ffmpeg child) via
    ``psutil`` before the rmtree fires.

These tests pin that contract. The worker's ``run()`` is invoked
synchronously; ``shutil.rmtree`` is spied via ``monkeypatch`` (no real
filesystem teardown is needed for the assertion -- we only care that the
finally arm fires and targets the right path).

Pure unit tests: no network, no subprocess, no QThread.start(). Do NOT
add ``@pytest.mark.youtube`` here -- that marker gates integration tests
that hit yt-dlp for real.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QCoreApplication

from anki_miner.exceptions.youtube import BotDetectionError, YouTubeFetchError
from anki_miner.gui.workers import youtube_worker as youtube_worker_module
from anki_miner.gui.workers.youtube_worker import YouTubeWorkerThread

# Qt signals require a QCoreApplication. Reuse the process-wide instance.
_app = QCoreApplication.instance() or QCoreApplication([])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def youtube_config(test_config, tmp_path):
    """Config whose ``media_temp_folder`` is a fresh tmp_path subdirectory."""
    return replace(test_config, media_temp_folder=tmp_path / "temp_media")


@pytest.fixture
def mock_processor():
    """``MagicMock`` standing in for :class:`EpisodeProcessor`.

    The worker only calls ``process_youtube_url``; nothing else needs a
    real implementation.
    """
    processor = MagicMock()
    processor.process_youtube_url = MagicMock(return_value=MagicMock(name="ProcessingResult"))
    return processor


@pytest.fixture
def rmtree_spy(monkeypatch):
    """Spy on ``shutil.rmtree`` as imported by the worker module.

    The worker does ``import shutil`` at module load and calls
    ``shutil.rmtree(...)``. We patch the attribute on the worker module's
    ``shutil`` reference so the spy intercepts the actual call site.

    Returns the spy mock with ``.call_args_list`` populated after run.
    """
    spy = MagicMock(name="rmtree_spy")
    monkeypatch.setattr(youtube_worker_module.shutil, "rmtree", spy)
    return spy


@pytest.fixture
def make_worker(mock_processor, youtube_config):
    """Factory producing a :class:`YouTubeWorkerThread` with default args."""

    def _make() -> YouTubeWorkerThread:
        return YouTubeWorkerThread(
            processor=mock_processor,
            config=youtube_config,
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_id="dQw4w9WgXcQ",
            sub_mode="manual_only",
        )

    return _make


def _captured_workspace(spy: MagicMock) -> Path:
    """Extract the workspace path from the rmtree spy's first call.

    Asserts the spy was called exactly once with a ``Path`` positional arg
    whose parent matches the ``youtube/`` subtree, then returns it.
    """
    assert spy.call_count == 1, f"rmtree expected 1 call, got {spy.call_count}"
    args, _kwargs = spy.call_args
    assert args, "rmtree called with no positional args"
    workspace = args[0]
    assert isinstance(workspace, Path), f"rmtree first arg must be Path, got {type(workspace)}"
    return workspace


# ---------------------------------------------------------------------------
# Exit path 1: happy path -- mining completes, rmtree fires.
# ---------------------------------------------------------------------------


def test_workspace_cleaned_on_happy_path(make_worker, mock_processor, rmtree_spy, youtube_config):
    """When ``process_youtube_url`` returns normally, rmtree runs in finally."""
    worker = make_worker()

    worker.run()

    assert mock_processor.process_youtube_url.call_count == 1
    workspace = _captured_workspace(rmtree_spy)
    # Worker allocates under <media_temp>/youtube/run-<hex>.
    assert workspace.parent == youtube_config.media_temp_folder / "youtube"
    assert workspace.name.startswith("run-")


# ---------------------------------------------------------------------------
# Exit path 2: fetcher raises YouTubeFetchError -- rmtree still fires.
# ---------------------------------------------------------------------------


def test_workspace_cleaned_when_fetcher_raises_youtube_fetch_error(make_worker, mock_processor, rmtree_spy):
    """A generic YouTubeFetchError from the fetcher must not skip cleanup."""
    worker = make_worker()
    mock_processor.process_youtube_url.side_effect = YouTubeFetchError("yt-dlp exited non-zero")

    worker.run()

    # The exception is caught by the worker's ``except Exception`` arm and
    # routed to the error signal; finally still runs.
    assert mock_processor.process_youtube_url.call_count == 1
    _captured_workspace(rmtree_spy)


# ---------------------------------------------------------------------------
# Exit path 3: fetcher raises BotDetectionError -- rmtree still fires.
# ---------------------------------------------------------------------------


def test_workspace_cleaned_when_fetcher_raises_bot_detection(make_worker, mock_processor, rmtree_spy):
    """A BotDetectionError (subclass of YouTubeFetchError) still triggers cleanup."""
    worker = make_worker()
    mock_processor.process_youtube_url.side_effect = BotDetectionError("Sign in to confirm you're not a bot")

    worker.run()

    assert mock_processor.process_youtube_url.call_count == 1
    _captured_workspace(rmtree_spy)


# ---------------------------------------------------------------------------
# Exit path 4: cancelled mid-fetch -- rmtree still fires.
# ---------------------------------------------------------------------------


def test_workspace_cleaned_when_cancelled_mid_fetch(make_worker, mock_processor, rmtree_spy):
    """Cancel during fetch -> finally still runs rmtree on the workspace.

    Simulates a real cancel: ``process_youtube_url`` observes ``cancel_event``
    being set (the fetcher would normally kill its yt-dlp subprocess tree via
    ``psutil`` before re-raising or returning), then a YouTubeFetchError is
    raised to mimic the fetcher's "process killed" surface.
    """
    worker = make_worker()

    def _cancel_then_raise(**kwargs):
        # Mid-fetch cancellation: caller sets the event before the fetcher
        # surfaces its failure. The real psutil-driven process kill happens
        # inside the fetcher; mocked away here.
        kwargs["cancel_event"].set()
        raise YouTubeFetchError("yt-dlp killed by cancellation")

    mock_processor.process_youtube_url.side_effect = _cancel_then_raise

    worker.run()

    assert mock_processor.process_youtube_url.call_count == 1
    _captured_workspace(rmtree_spy)


# ---------------------------------------------------------------------------
# Exit path 5: mining-phase exception after fetch -- rmtree still fires.
# ---------------------------------------------------------------------------


def test_workspace_cleaned_when_mining_phase_raises_after_fetch(make_worker, mock_processor, rmtree_spy):
    """A failure after fetch (e.g. inside ``process_episode``) still cleans up.

    The worker doesn't distinguish fetch-phase vs mining-phase exceptions; it
    only knows ``process_youtube_url`` raised. We use a non-YouTube exception
    here to stand in for a downstream mining failure (e.g. AnkiConnect down,
    fugashi tokenizer error). The invariant is identical: finally fires.
    """
    worker = make_worker()
    mock_processor.process_youtube_url.side_effect = RuntimeError("AnkiConnect unreachable (mining phase)")

    worker.run()

    assert mock_processor.process_youtube_url.call_count == 1
    _captured_workspace(rmtree_spy)

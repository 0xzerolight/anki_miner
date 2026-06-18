"""Tests for SingleEpisodeTab.release_dictionary_resources (Issue #30 follow-up).

The shipped #30 fix closed YouTubeTab's cached processor handles but left
SingleEpisodeTab as a no-op. After a mine completes, the finished
``EpisodeWorkerThread`` retains its processor (with open sqlite handles),
exposed through the typed ``curation_processor`` property (T-60), until a
new run replaces it. The tab closes the handles through the
``EpisodeProcessor.release_dictionary_resources`` facade so the Win11 user
can delete or re-import a dictionary after mining without hitting the
file-lock error.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab


@pytest.fixture
def tab(qapp, qtbot, test_config):
    widget = SingleEpisodeTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def _idle_worker(processor):
    """Build a MagicMock worker exposing ``processor`` via ``curation_processor``."""
    worker = MagicMock(name="EpisodeWorkerThread")
    worker.isRunning.return_value = False
    worker.curation_processor = processor
    return worker


def test_release_when_no_worker_returns_true(tab):
    tab.worker_thread = None
    assert tab.release_dictionary_resources() is True


def test_release_with_idle_worker_closes_definition_service_via_facade(tab, facade_processor):
    tab.worker_thread = _idle_worker(facade_processor)

    assert tab.release_dictionary_resources() is True
    facade_processor.definition_service.close.assert_called_once_with()


def test_release_with_running_worker_returns_false(tab, facade_processor):
    worker = _idle_worker(facade_processor)
    worker.isRunning.return_value = True
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is False
    facade_processor.definition_service.close.assert_not_called()


def test_release_with_idle_worker_no_processor_returns_true(tab):
    tab.worker_thread = _idle_worker(None)
    assert tab.release_dictionary_resources() is True


def test_release_idempotent(tab, facade_processor):
    tab.worker_thread = _idle_worker(facade_processor)

    assert tab.release_dictionary_resources() is True
    assert tab.release_dictionary_resources() is True
    assert facade_processor.definition_service.close.call_count == 2


# ---------------------------------------------------------------------------
# Sequential-rerun teardown (Windows back-to-back-mining freeze)
# ---------------------------------------------------------------------------


def _ready_tab_inputs(tab, tmp_path):
    video = tmp_path / "ep01.mkv"
    video.touch()
    subs = tmp_path / "ep01.ass"
    subs.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)


def test_rerun_closes_prior_processor_before_building_new_one(tab, tmp_path):
    """Run N+1 closes run N's processor before the new processor is built.

    With the factory path the new processor is built lazily on the worker
    thread (not synchronously on the GUI thread during _start_processing), so
    the old_close → build_new ordering is enforced across the thread boundary:
    teardown is synchronous (old_close happens in _start_processing) while
    build_new happens only when the factory is invoked inside worker.run().
    We verify the GUI-thread half: old processor closed during _start_processing,
    and create_episode_processor NOT called synchronously on the GUI thread.
    """
    from unittest.mock import patch

    _ready_tab_inputs(tab, tmp_path)

    old_processor = MagicMock(name="OldProcessor")
    old_worker = MagicMock(name="OldWorker")
    old_worker.wait.return_value = True
    old_worker.curation_processor = old_processor
    tab.worker_thread = old_worker

    new_worker = MagicMock(name="NewWorker")
    new_processor = MagicMock(name="NewProcessor")

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=new_worker) as worker_cls,
        patch(
            "anki_miner.gui.widgets.single_episode_tab.create_episode_processor",
            return_value=new_processor,
        ) as mock_build,
    ):
        tab._start_processing(preview_mode=False)

    # Old worker was cancelled and joined.
    old_worker.cancel.assert_called_once_with()
    old_worker.wait.assert_called_once()
    # Old processor was closed (teardown happened synchronously on GUI thread).
    old_processor.close.assert_called_once_with()
    # create_episode_processor is NOT called synchronously on the GUI thread;
    # it only runs inside the factory closure when the worker calls run().
    mock_build.assert_not_called()
    # A processor_factory keyword arg was passed to the new worker.
    _, kwargs = worker_cls.call_args
    assert callable(kwargs.get("processor_factory")), "processor_factory must be a callable"
    assert kwargs.get("processor") is None, "processor must be None when factory is used"


def test_rerun_with_no_prior_worker_does_not_crash(tab, tmp_path):
    from unittest.mock import patch

    _ready_tab_inputs(tab, tmp_path)
    tab.worker_thread = None

    with (
        patch(
            "anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread",
            return_value=MagicMock(),
        ),
        patch(
            "anki_miner.gui.widgets.single_episode_tab.create_episode_processor",
            return_value=MagicMock(),
        ),
    ):
        tab._start_processing(preview_mode=False)  # must not raise


def test_teardown_disconnects_finished_handler(tab, tmp_path):
    """A late worker termination must not restore buttons mid-new-run."""
    old_worker = MagicMock(name="OldWorker")
    old_worker.wait.return_value = True
    old_worker.curation_processor = MagicMock()
    tab.worker_thread = old_worker

    tab._teardown_previous_run("single-episode")

    old_worker.finished.disconnect.assert_called_once_with(tab._restore_buttons)


def test_rerun_skips_processor_close_on_join_timeout(tab, tmp_path):
    """On wait() timeout the worker is still live; closing its sqlite handles
    from the GUI thread would race the worker — so the close is SKIPPED while
    the new processor is still built and ``self.worker_thread`` reassigned."""
    from unittest.mock import patch

    _ready_tab_inputs(tab, tmp_path)

    old_processor = MagicMock(name="OldProcessor")
    old_worker = MagicMock(name="OldWorker")
    old_worker.wait.return_value = False  # join times out → worker still running
    old_worker.curation_processor = old_processor
    tab.worker_thread = old_worker

    new_worker = MagicMock(name="NewWorker")
    new_processor = MagicMock(name="NewProcessor")

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=new_worker),
        patch(
            "anki_miner.gui.widgets.single_episode_tab.create_episode_processor",
            return_value=new_processor,
        ),
    ):
        tab._start_processing(preview_mode=False)

    old_worker.cancel.assert_called_once_with()
    old_worker.wait.assert_called_once()
    # MUST NOT close the old processor under a still-running worker.
    old_processor.close.assert_not_called()
    # New run still proceeds: processor built and worker reassigned.
    assert tab.worker_thread is new_worker

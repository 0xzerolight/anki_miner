"""A cancel asked for from elsewhere still gets performed by the owning screen.

The mini job monitor can stop a run it has no route to, because the registry
relays the ask and the screen that started the run does the stopping — through
exactly the handler its own Cancel button uses. Two things have to hold for that
to be true rather than merely plausible:

* the relay must be filtered, so a request for one screen's run is not acted on
  by another, and a request for a run that already ended does not re-enter a
  cancel path whose worker is gone;
* every screen that publishes a run must actually override the hook. The default
  is a no-op, so a screen that quietly inherits it would ship a Cancel button
  that does nothing — which is worse than no button. The ledger below is what
  keeps that from happening silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import anki_miner.gui.widgets
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry
from anki_miner.gui.widgets.base.task_publisher import TaskPublisherMixin


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


class _Screen(TaskPublisherMixin):
    """The smallest thing that publishes a run and can be asked to stop it."""

    TASK_ID = "run.single"
    TASK_OWNER = CapabilityTarget("video", "single")

    def __init__(self) -> None:
        self.cancels = 0

    def _cancel_published_task(self) -> None:
        self.cancels += 1


class _OtherScreen(_Screen):
    TASK_ID = "queue.youtube"
    TASK_OWNER = CapabilityTarget("video", "youtube")


class TestTheRelayIsFiltered:
    def test_a_request_for_this_screen_stops_its_run(self, registry):
        screen = _Screen()
        screen.bind_task_registry(registry)
        screen._publish_task_start("Mining Samurai Champloo")

        registry.request_cancel("run.single")

        assert screen.cancels == 1

    def test_a_request_for_another_screen_is_ignored(self, registry):
        screen = _Screen()
        other = _OtherScreen()
        screen.bind_task_registry(registry)
        other.bind_task_registry(registry)
        screen._publish_task_start("Mining Samurai Champloo")
        other._publish_task_start("YouTube queue")

        registry.request_cancel("queue.youtube")

        assert (screen.cancels, other.cancels) == (0, 1)

    def test_a_request_for_a_finished_run_is_ignored(self, registry):
        screen = _Screen()
        screen.bind_task_registry(registry)
        screen._publish_task_start("Mining Samurai Champloo")
        screen._publish_task_finish(TaskOutcome.SUCCEEDED)
        # The run is gone from the screen; the registry would decline it too, so
        # ask past the registry's own guard to prove the screen guards as well.
        registry.cancel_requested.emit("run.single")

        assert screen.cancels == 0

    def test_a_screen_that_never_ran_is_ignored(self, registry):
        screen = _Screen()
        screen.bind_task_registry(registry)

        registry.cancel_requested.emit("run.single")

        assert screen.cancels == 0

    def test_a_screen_that_publishes_nothing_subscribes_to_nothing(self, registry):
        class _Silent(_Screen):
            TASK_ID = ""

        screen = _Silent()
        screen.bind_task_registry(registry)

        registry.cancel_requested.emit("")

        assert screen.cancels == 0

    def test_the_relay_leaves_the_snapshot_to_the_screen(self, registry):
        """The screen reports ``cancelling``; the relay does not do it for it."""
        screen = _Screen()
        screen.bind_task_registry(registry)
        screen._publish_task_start("Mining Samurai Champloo")

        registry.request_cancel("run.single")

        snap = registry.snapshot("run.single")
        assert snap is not None
        assert snap.cancelling is False


def _publishing_screens():
    """Every concrete screen that declares a task id, imported lazily."""
    from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
    from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
    from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
    from anki_miner.gui.widgets.condense_tab import CondenseTab
    from anki_miner.gui.widgets.deck_filter_tab import DeckFilterTab
    from anki_miner.gui.widgets.download_tab import DownloadTab
    from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
    from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
    from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
    from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
    from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
    from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
    from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab
    from anki_miner.gui.widgets.youtube_tab import YouTubeTab

    return (
        SingleEpisodeTab,
        BatchProcessingTab,
        YouTubeTab,
        AudiobookTab,
        ReadingMangaTab,
        ReadingNovelsTab,
        ReadingSubtitlesTab,
        ReadingTextTab,
        CondenseTab,
        SubtitleCreationTab,
        SubtitleRetimeTab,
        CardBackfillTab,
        DeckFilterTab,
        DownloadTab,
    )


class TestEveryPublishingScreenCanBeStopped:
    def test_no_screen_inherits_the_no_op_hook(self):
        inherited = [
            cls.__name__
            for cls in _publishing_screens()
            if cls._cancel_published_task is TaskPublisherMixin._cancel_published_task
        ]
        assert inherited == []

    def test_the_ledger_covers_every_screen_that_declares_a_task_id(self):
        """A new publishing screen has to be added here, not silently skipped.

        Read off the source rather than off the ledger's own imports, so a
        screen that starts publishing without being listed fails here instead of
        shipping a Cancel that reaches it and does nothing.
        """
        declared = {
            match.group(1)
            for path in (Path(anki_miner.gui.widgets.__file__).parent).rglob("*.py")
            for match in re.finditer(r'^\s*TASK_ID(?::\s*str)?\s*=\s*"([^"]+)"', path.read_text(), re.MULTILINE)
        }
        listed = {cls.TASK_ID for cls in _publishing_screens()}
        # The recommended-resource session is not a screen and not a mixin; it
        # honours the same request through its own registry subscription.
        assert declared - listed == {"resource-download"}

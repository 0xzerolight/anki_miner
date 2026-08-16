"""A fresh attempt supersedes the complaint about the previous one (D24).

The banner was built so a failure during an unattended run is still on screen
hours later, and clearing it was left to "the caller, on success". That worked
for the screens whose success path is a single callback (Settings, Analytics)
and failed everywhere else: the mining and file-tool screens raise banners from
*input validation*, which has no success callback at all, so a refusal such as
"No valid series in the queue to process." outlived the run the user then went
on to complete. Ninety-six reporting sites shared fifteen clear sites.

The rule these tests pin: pressing a screen's run or probe action clears that
screen's banner first, so the sentence on display always describes the attempt
the user just made. The banner is still never on a timer -- see
``tests/unit/test_screen_issue_banner.py``.

The one ordering hazard is pinned at the bottom: the clear belongs at the
*entry*, not at ``_publish_task_start``, because Batch raises its "was skipped"
warnings in between.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.base.screen_issue_banner import ScreenIssue
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab

_FFMPEG_AVAILABLE = "anki_miner.gui.widgets.condense_tab.CondenseTab._compute_ffmpeg_available"
_ALASS_AVAILABLE = "anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeTab._compute_alass_available"
_ENGINE_AVAILABLE = "anki_miner.services.asr._engine.available"

#: Stands in for whatever the *previous* attempt complained about. Deliberately
#: a sentence no entry point under test can re-raise, so surviving it is proof
#: the banner was never cleared rather than proof it was cleared and refilled.
STALE = ScreenIssue(summary="Something the last attempt objected to.")


def _mining_tab(cls, test_config, qtbot):
    widget = cls(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def batch_tab(qapp, qtbot, test_config):
    return _mining_tab(BatchProcessingTab, test_config, qtbot)


@pytest.fixture
def single_tab(qapp, qtbot, test_config):
    return _mining_tab(SingleEpisodeTab, test_config, qtbot)


@pytest.fixture
def condense_tab(qapp, qtbot, test_config):
    with patch(_FFMPEG_AVAILABLE, return_value=True):
        tab = CondenseTab(test_config)
        qtbot.addWidget(tab)
        assert tab._availability_worker is not None
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(tab.condense_button.isEnabled, timeout=3000)
    return tab


@pytest.fixture
def retime_tab(qapp, qtbot, test_config):
    with patch(_ALASS_AVAILABLE, return_value=True):
        tab = SubtitleRetimeTab(test_config)
        qtbot.addWidget(tab)
        assert tab._availability_worker is not None
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(tab.retime_button.isEnabled, timeout=3000)
    return tab


@pytest.fixture
def creation_tab(qapp, qtbot, test_config):
    with patch(_ENGINE_AVAILABLE, return_value=True):
        tab = SubtitleCreationTab(test_config)
        qtbot.addWidget(tab)
        assert tab._availability_worker is not None
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(tab.generate_button.isEnabled, timeout=3000)
    return tab


def _surviving_summary(tab) -> str | None:
    issue = tab.issue_banner().current_issue()
    return None if issue is None else issue.summary


#: ``(id, fixture, entry method, owner of the first post-clear call, its name,
#: what to return from it)``. The sixth element short-circuits the entry point
#: the instant it has been observed, so no worker, dialog or probe ever starts.
#:
#: Reading the banner *inside* that first call is what makes these tests mean
#: something. Asserting on the state afterwards would not: every entry point
#: goes on to raise its own refusal, and ``show_issue`` replaces, so a screen
#: that never cleared anything still ends up showing a different sentence.
#: Only the reading taken between the clear and the first validation branch
#: tells replacement and clearing apart.
ENTRY_POINTS = [
    ("batch: process queue", "batch_tab", "_process_queue", lambda t: t.queue_panel, "runnable_items", []),
    ("batch: process pairs", "batch_tab", "_process_pairs", lambda t: t, "_get_validated_folders", None),
    ("single: mine", "single_tab", "_start_processing", lambda t: t.video_selector, "path_or_none", None),
    ("single: audio tracks", "single_tab", "_on_tracks_clicked", lambda t: t.video_selector, "path_or_none", None),
    ("single: test timing", "single_tab", "_on_timing_clicked", lambda t: t.video_selector, "path_or_none", None),
    ("condense: condense", "condense_tab", "_on_condense", lambda t: t.log_widget, "clear_log", None),
    (
        "condense: audio tracks",
        "condense_tab",
        "_on_audio_tracks_clicked",
        lambda t: t.media_file_selector,
        "path_or_none",
        None,
    ),
    (
        "condense: subtitle tracks",
        "condense_tab",
        "_on_subtitle_tracks_clicked",
        lambda t: t.media_file_selector,
        "path_or_none",
        None,
    ),
    ("retime: retime", "retime_tab", "_on_retime", lambda t: t.log_widget, "clear_log", None),
    (
        "retime: reference",
        "retime_tab",
        "_on_tracks_clicked",
        lambda t: t.video_file_selector,
        "path_or_none",
        None,
    ),
    ("creation: generate", "creation_tab", "_on_generate", lambda t: t, "_collect_video_files", []),
]


@pytest.mark.parametrize(
    ("fixture", "entry", "owner_of", "first_call", "returns"),
    [pytest.param(*case[1:], id=case[0]) for case in ENTRY_POINTS],
)
def test_the_entry_point_clears_before_it_validates(request, fixture, entry, owner_of, first_call, returns):
    """Press the action with the previous attempt's complaint on screen, and
    read the banner at the first step the entry point takes after clearing."""
    tab = request.getfixturevalue(fixture)
    tab.show_screen_issue(STALE)
    seen: list[str | None] = []

    def _record(*_args, **_kwargs):
        seen.append(_surviving_summary(tab))
        return returns

    with patch.object(owner_of(tab), first_call, _record):
        getattr(tab, entry)()

    assert seen, f"{entry} never reached {first_call}"
    assert seen[0] is None, f"{entry} carried the previous attempt's issue past its own entry"


class TestTheReportedCase:
    def test_an_empty_queue_still_says_so(self, batch_tab):
        """Superseding must not swallow the refusal it supersedes: press Process
        Queue on an empty queue twice and the sentence is there both times."""
        batch_tab._process_queue()
        first = _surviving_summary(batch_tab)
        assert first is not None
        batch_tab._process_queue()
        assert _surviving_summary(batch_tab) == first

    def test_a_run_that_starts_leaves_no_refusal_behind(self, batch_tab):
        """The bug as reported: refuse, fix the queue, run, and the complaint
        about the empty queue is gone rather than sitting over a working run."""
        batch_tab._process_queue()
        assert _surviving_summary(batch_tab) is not None

        batch_tab.queue_panel.runnable_items = MagicMock(return_value=[MagicMock(name="QueueItem")])
        batch_tab.queue_panel.get_incomplete_items = MagicMock(return_value=[])
        with patch.object(batch_tab, "_start_queue_worker"):
            batch_tab._process_queue()

        assert _surviving_summary(batch_tab) is None


class TestClearPlacement:
    """Why the clear sits at the entry point and not at the obvious choke point.

    ``TaskPublisherMixin._publish_task_start`` looks like the one place every
    run passes through. It is not usable: ``_process_queue`` raises its
    per-series "was skipped" warnings through ``_warn_incomplete_items``
    *before* ``_start_queue_worker`` ever reaches the publish call, so a clear
    there would erase the warnings the run had just produced.
    """

    def test_skipped_series_warnings_survive_the_run_starting(self, batch_tab):
        skipped = MagicMock(name="QueueItemWidget")
        skipped.display_name = "Frieren S1"
        batch_tab.queue_panel.runnable_items = MagicMock(return_value=[MagicMock(name="QueueItem")])
        batch_tab.queue_panel.get_incomplete_items = MagicMock(return_value=[(skipped, "invalid")])

        with patch.object(batch_tab, "_start_queue_worker"):
            batch_tab._process_queue()

        surviving = _surviving_summary(batch_tab)
        assert surviving is not None
        assert "Frieren S1" in surviving

    def test_a_click_during_a_live_run_keeps_that_runs_problem(self, batch_tab):
        """The clear sits *after* the reentrancy guard, so a second press while
        a run is in flight cannot wipe the problem that run is reporting."""
        batch_tab.show_screen_issue(STALE)
        batch_tab._is_processing = True
        batch_tab._process_queue()
        assert _surviving_summary(batch_tab) == STALE.summary

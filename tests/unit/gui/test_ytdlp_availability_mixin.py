"""Tests for the shared yt-dlp availability state (Utilities -> Download, Video -> YouTube).

The mixin owns the three states a screen can be in — usable, missing, download in
flight — and nothing else: the probe dispatch and the signal belong to the host.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from anki_miner.gui.widgets.base.ytdlp_availability import (
    YTDLP_DOWNLOAD_ACTION,
    YtdlpAvailabilityMixin,
    YtdlpStrings,
)


class _Host(YtdlpAvailabilityMixin, QWidget):
    """The smallest screen the mixin supports: a banner and the two hooks."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout()
        self.setLayout(layout)
        self.install_issue_banner(layout)
        self.refreshes = 0
        self.download_requests = 0
        self._ytdlp_strings = YtdlpStrings(
            missing="yt-dlp is not installed.",
            download_action="Download yt-dlp",
            downloading="Downloading yt-dlp…",
        )

    def _refresh_ytdlp_state(self) -> None:
        self.refreshes += 1

    def _emit_ytdlp_download_requested(self) -> None:
        self.download_requests += 1


@pytest.fixture
def host(qtbot) -> _Host:
    widget = _Host()
    qtbot.addWidget(widget)
    return widget


def test_a_missing_binary_offers_a_download(host: _Host) -> None:
    host._apply_probe_result(False)

    issue = host.issue_banner().current_issue()
    assert issue is not None
    assert issue.action_id == YTDLP_DOWNLOAD_ACTION
    assert issue.action_text == "Download yt-dlp"
    assert host._ytdlp_ready() is False


def test_an_unprobed_screen_is_not_known_missing(host: _Host) -> None:
    """The cached default is "not available"; only an answered probe refuses a run."""
    assert host._ytdlp_known_missing() is False

    host._apply_probe_result(False)

    assert host._ytdlp_known_missing() is True


def test_a_usable_binary_says_nothing(host: _Host) -> None:
    host._apply_probe_result(True)

    assert host.issue_banner().current_issue() is None
    assert host._ytdlp_ready() is True


def test_the_action_starts_one_download_and_shows_progress(host: _Host) -> None:
    host._apply_probe_result(False)

    host.issue_banner().action_button.click()

    assert host.download_requests == 1
    issue = host.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "Downloading yt-dlp…"
    assert issue.action_text == "", "a download in flight must not offer a second one"


def test_a_success_while_pending_clears_the_downloading_banner(host: _Host) -> None:
    """The in-progress issue must still be recognized as ours once it succeeds."""
    host._apply_probe_result(False)
    host.issue_banner().action_button.click()

    host._apply_probe_result(True)

    assert host.issue_banner().current_issue() is None


def test_an_update_result_reprobes_and_leaves_the_pending_state(host: _Host) -> None:
    host._apply_probe_result(False)
    host.issue_banner().action_button.click()

    host.notify_ytdlp_update_result(object())

    assert host.refreshes == 1
    assert host._ytdlp_download_pending is False


def test_it_never_clears_an_issue_it_did_not_raise(host: _Host) -> None:
    from anki_miner.gui.widgets.base.screen_issue_banner import ScreenIssue

    host.show_screen_issue(ScreenIssue(summary="The download folder is not writable."))

    host._apply_probe_result(True)

    issue = host.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "The download folder is not writable."


def test_it_never_clears_an_issue_that_superseded_its_own(host: _Host) -> None:
    """One banner per screen: a later failure replaced ours, so it is not ours to drop."""
    from anki_miner.gui.widgets.base.screen_issue_banner import ScreenIssue

    host._apply_probe_result(False)
    host.show_screen_issue(ScreenIssue(summary="The download folder is not writable."))

    host._apply_probe_result(True)

    issue = host.issue_banner().current_issue()
    assert issue is not None
    assert issue.summary == "The download folder is not writable."


def test_a_failed_download_says_so_and_offers_the_action_again(host: _Host) -> None:
    from types import SimpleNamespace

    host._apply_probe_result(False)
    host.issue_banner().action_button.click()

    host.notify_ytdlp_update_result(SimpleNamespace(action="failed", message="yt-dlp update failed: no route to host"))
    host._apply_probe_result(False)

    issue = host.issue_banner().current_issue()
    assert issue is not None
    assert issue.action_id == YTDLP_DOWNLOAD_ACTION
    assert "no route to host" in issue.details


def test_a_result_for_a_download_this_screen_did_not_ask_for_adds_no_details(host: _Host) -> None:
    from types import SimpleNamespace

    host._apply_probe_result(False)

    host.notify_ytdlp_update_result(SimpleNamespace(action="failed", message="yt-dlp update failed: no route to host"))
    host._apply_probe_result(False)

    assert host.issue_banner().current_issue().details == ""

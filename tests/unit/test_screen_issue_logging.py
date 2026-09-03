"""Every recoverable problem reaches the log, delivered to a banner or not.

The banner is the user's half of decision D24; this is the diagnostic half. A
support report that says "the app told me nothing" is indistinguishable, in a
log without these records, from a run that had nothing to say. So each
:class:`ScreenIssue` is mirrored to ``anki_miner.gui.issues`` with a
``delivered`` field: ``no`` means the issue was raised on a screen that had no
banner installed, and the user genuinely saw nothing.

There are exactly three reporting paths (banner show, host without a banner,
controller without a host), so mirroring them covers all 106 ``ScreenIssue``
constructions without touching a single reporting site.
"""

import logging

import pytest
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from anki_miner.gui.widgets.base.screen_issue_banner import (
    ISSUE_LOGGER_NAME,
    ScreenIssue,
    ScreenIssueBanner,
    ScreenIssueHost,
    report_screen_issue,
)

FFMPEG = ScreenIssue(
    summary="ffmpeg was not found.",
    details="/usr/bin/ffmpeg missing",
    action_id="open-media-settings",
    action_text="Open Media Settings",
)


class SettingsTab(ScreenIssueHost, QWidget):
    """A screen that installs a banner, named so the log field is checkable."""

    def __init__(self):
        super().__init__()
        self.setLayout(QVBoxLayout())
        self.install_issue_banner(self.layout())


class BannerlessTab(ScreenIssueHost, QWidget):
    """A migrated-later screen: reporting is inert, but must not be silent."""


@pytest.fixture
def issue_log(caplog):
    """Capture the issue mirror at DEBUG, restoring levels afterwards.

    Yields ``caplog`` rather than ``caplog.records``: that list is rebound per
    test phase, so a reference grabbed during setup stays empty for the call.
    """
    with caplog.at_level(logging.DEBUG, logger=ISSUE_LOGGER_NAME):
        yield caplog


def _records(caplog):
    return [r for r in caplog.records if r.name == ISSUE_LOGGER_NAME]


def _messages(caplog):
    return [r.getMessage() for r in _records(caplog)]


class TestDelivered:
    def test_banner_show_logs_a_warning_with_every_field(self, qtbot, issue_log):
        screen = SettingsTab()
        qtbot.addWidget(screen)
        screen.show_screen_issue(FFMPEG)

        (record,) = _records(issue_log)
        assert record.levelno == logging.WARNING
        assert record.getMessage() == (
            'Screen issue: screen=SettingsTab summary="ffmpeg was not found." '
            'details="/usr/bin/ffmpeg missing" action=open-media-settings delivered=yes'
        )

    def test_the_record_is_attributed_to_the_reporting_line(self, qtbot, issue_log):
        screen = SettingsTab()
        qtbot.addWidget(screen)
        screen.show_screen_issue(FFMPEG)

        (record,) = _records(issue_log)
        assert record.module == "screen_issue_banner"

    def test_a_bare_banner_reports_an_empty_screen_name(self, qtbot, issue_log):
        banner = ScreenIssueBanner()
        qtbot.addWidget(banner)
        banner.show_issue(ScreenIssue(summary="x"))

        assert _messages(issue_log) == ["Screen issue: screen=- summary=x details=- action=- delivered=yes"]


class TestUndelivered:
    def test_a_host_without_a_banner_still_logs_the_issue(self, qtbot, issue_log):
        screen = BannerlessTab()
        qtbot.addWidget(screen)
        screen.show_screen_issue(FFMPEG)

        (record,) = _records(issue_log)
        assert record.levelno == logging.WARNING
        assert "screen=BannerlessTab" in record.getMessage()
        assert "delivered=no" in record.getMessage()

    def test_a_controller_with_no_host_logs_and_reports_failure(self, issue_log):
        assert report_screen_issue(None, ScreenIssue(summary="x")) is False

        (record,) = _records(issue_log)
        assert record.levelno == logging.WARNING
        assert record.getMessage() == "Screen issue: screen=- summary=x details=- action=- delivered=no"

    def test_an_orphan_origin_names_the_widget_it_was_raised_from(self, qtbot, issue_log):
        origin = QWidget()
        qtbot.addWidget(origin)

        assert report_screen_issue(origin, ScreenIssue(summary="x")) is False
        assert "screen=QWidget" in _messages(issue_log)[0]

    def test_a_delivered_issue_is_logged_once_only(self, qtbot, issue_log):
        screen = SettingsTab()
        qtbot.addWidget(screen)

        assert report_screen_issue(screen.issue_banner(), FFMPEG) is True
        assert len(_messages(issue_log)) == 1
        assert "delivered=yes" in _messages(issue_log)[0]


class TestCleared:
    def test_clearing_logs_at_debug_with_the_issue_it_removed(self, qtbot, issue_log):
        screen = SettingsTab()
        qtbot.addWidget(screen)
        screen.show_screen_issue(FFMPEG)
        issue_log.clear()

        screen.clear_screen_issue()

        (record,) = _records(issue_log)
        assert record.levelno == logging.DEBUG
        assert record.getMessage() == ('Screen issue cleared: screen=SettingsTab summary="ffmpeg was not found."')

    def test_clearing_an_empty_banner_says_so(self, qtbot, issue_log):
        banner = ScreenIssueBanner()
        qtbot.addWidget(banner)

        banner.clear_issue()

        assert _messages(issue_log) == ["Screen issue cleared: screen=- summary=-"]

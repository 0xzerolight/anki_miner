"""The persistent screen-scoped issue banner (decision D24).

A recoverable problem must not stop an unattended run, so it is reported where
it happened and stays there until it is fixed. The three properties every test
here defends: the banner *persists*, the repair action is *inside* it, and the
raw diagnostic is *behind* Details rather than in the sentence the user reads.
"""

import pytest
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from anki_miner.gui.widgets.base.screen_issue_banner import (
    ScreenIssue,
    ScreenIssueBanner,
    ScreenIssueHost,
    clear_reported_issue,
    report_screen_issue,
)

FFMPEG = ScreenIssue(
    summary="ffmpeg and ffprobe weren't found.",
    details="FileNotFoundError: [Errno 2] No such file or directory: 'ffprobe'",
    action_id="settings.media",
    action_text="Open Media Settings",
)


@pytest.fixture
def banner(qtbot):
    widget = ScreenIssueBanner()
    qtbot.addWidget(widget)
    return widget


class TestPresentation:
    def test_starts_empty_and_hidden(self, banner):
        assert banner.current_issue() is None
        assert banner.isHidden()

    def test_showing_an_issue_reveals_the_summary(self, banner, qtbot):
        banner.show_issue(FFMPEG)
        assert banner.current_issue() == FFMPEG
        assert banner.summary_label.text() == "ffmpeg and ffprobe weren't found."
        assert not banner.isHidden()

    def test_summary_never_carries_the_raw_diagnostic(self, banner):
        banner.show_issue(FFMPEG)
        assert "FileNotFoundError" not in banner.summary_label.text()

    def test_action_text_labels_the_repair_button(self, banner):
        banner.show_issue(FFMPEG)
        assert banner.action_button.text() == "Open Media Settings"
        assert not banner.action_button.isHidden()

    def test_issue_without_an_action_hides_the_button(self, banner):
        banner.show_issue(ScreenIssue(summary="Audio tracks could not be read."))
        assert banner.action_button.isHidden()

    def test_empty_summary_is_rejected(self):
        with pytest.raises(ValueError):
            ScreenIssue(summary="")


class TestDetails:
    def test_details_are_collapsed_until_asked_for(self, banner):
        banner.show_issue(FFMPEG)
        assert not banner.details_button.isHidden()
        assert banner.details_label.isHidden()

    def test_expanding_shows_the_raw_diagnostic(self, banner):
        banner.show_issue(FFMPEG)
        banner.details_button.click()
        assert not banner.details_label.isHidden()
        assert "FileNotFoundError" in banner.details_label.text()

    def test_an_issue_with_no_details_offers_no_expander(self, banner):
        banner.show_issue(ScreenIssue(summary="Audio tracks could not be read."))
        assert banner.details_button.isHidden()
        assert banner.details_label.isHidden()

    def test_a_new_issue_re_collapses_details(self, banner):
        banner.show_issue(FFMPEG)
        banner.details_button.click()
        banner.show_issue(ScreenIssue(summary="Settings could not be exported.", details="PermissionError"))
        assert banner.details_label.isHidden()
        assert not banner.details_button.isChecked()


class TestPersistenceAndAction:
    def test_the_banner_survives_until_it_is_cleared(self, banner):
        banner.show_issue(FFMPEG)
        banner.details_button.click()
        assert not banner.isHidden()
        banner.clear_issue()
        assert banner.isHidden()
        assert banner.current_issue() is None

    def test_clearing_collapses_details_for_the_next_issue(self, banner):
        banner.show_issue(FFMPEG)
        banner.details_button.click()
        banner.clear_issue()
        banner.show_issue(FFMPEG)
        assert banner.details_label.isHidden()

    def test_the_repair_button_emits_the_action_id(self, banner, qtbot):
        banner.show_issue(FFMPEG)
        with qtbot.waitSignal(banner.action_requested) as blocker:
            banner.action_button.click()
        assert blocker.args == ["settings.media"]


class _Screen(ScreenIssueHost, QWidget):
    """A screen shaped like the real ones: one top-level vertical layout."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        layout.addWidget(QWidget())
        self.setLayout(layout)
        self.install_issue_banner(layout)


class TestHost:
    def test_the_banner_is_installed_above_the_page(self, qtbot):
        screen = _Screen()
        qtbot.addWidget(screen)
        assert screen.layout().itemAt(0).widget() is screen.issue_banner()

    def test_the_host_shows_and_clears_through_the_banner(self, qtbot):
        screen = _Screen()
        qtbot.addWidget(screen)
        screen.show_screen_issue(FFMPEG)
        assert screen.issue_banner().current_issue() == FFMPEG
        screen.clear_screen_issue()
        assert screen.issue_banner().current_issue() is None

    def test_the_action_runs_the_callback_the_caller_supplied(self, qtbot):
        screen = _Screen()
        qtbot.addWidget(screen)
        ran = []
        screen.show_screen_issue(FFMPEG, action=lambda: ran.append("repair"))
        screen.issue_banner().action_button.click()
        assert ran == ["repair"]

    def test_a_replacement_issue_replaces_the_callback(self, qtbot):
        screen = _Screen()
        qtbot.addWidget(screen)
        ran = []
        screen.show_screen_issue(FFMPEG, action=lambda: ran.append("first"))
        screen.show_screen_issue(FFMPEG, action=lambda: ran.append("second"))
        screen.issue_banner().action_button.click()
        assert ran == ["second"]

    def test_a_host_without_a_banner_is_a_safe_no_op(self, qtbot):
        class _Bare(ScreenIssueHost, QWidget):
            pass

        screen = _Bare()
        qtbot.addWidget(screen)
        screen.show_screen_issue(FFMPEG)
        screen.clear_screen_issue()
        assert screen.issue_banner() is None


class TestReporting:
    def test_a_child_reports_to_the_nearest_hosting_ancestor(self, qtbot):
        screen = _Screen()
        qtbot.addWidget(screen)
        child = QWidget(screen)
        grandchild = QWidget(child)
        assert report_screen_issue(grandchild, FFMPEG) is True
        assert screen.issue_banner().current_issue() == FFMPEG

    def test_reporting_from_the_host_itself_works(self, qtbot):
        screen = _Screen()
        qtbot.addWidget(screen)
        assert report_screen_issue(screen, FFMPEG) is True

    def test_reporting_with_no_host_says_so_instead_of_raising(self, qtbot):
        orphan = QWidget()
        qtbot.addWidget(orphan)
        assert report_screen_issue(orphan, FFMPEG) is False

    def test_reporting_with_no_origin_says_so(self):
        assert report_screen_issue(None, FFMPEG) is False

    def test_clearing_through_a_child_clears_the_host(self, qtbot):
        screen = _Screen()
        qtbot.addWidget(screen)
        child = QWidget(screen)
        report_screen_issue(child, FFMPEG)
        assert clear_reported_issue(child) is True
        assert screen.issue_banner().current_issue() is None

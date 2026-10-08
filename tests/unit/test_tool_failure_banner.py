"""E13: a tool failure banner says what failed, plainly.

A one-file Condense run that failed said "Some files could not be condensed."
A typed error the tool knows becomes its own translated sentence; the raw text
goes under Details; a repair is offered only where one exists.
"""

from __future__ import annotations

import pytest

from anki_miner.exceptions import YtdlpNotFoundError
from anki_miner.services.audio_condenser import FilterUnavailableError
from tests.unit._tool_tab_harness import FakeToolWorker, capture_slots
from tests.unit.test_tool_tab_contract import _CONDENSE, _DOWNLOAD, _RETIME, _make_tab, _start_single_run


def _fail_one(spec, qtbot, tmp_path, *, fatal=None, total=None):
    tab = _make_tab(spec, qtbot, tmp_path)
    worker = FakeToolWorker()
    if fatal is not None:
        worker.fatal_exception = fatal
    finished = capture_slots(worker.file_finished)
    _start_single_run(spec, tab, tmp_path, worker, qtbot)
    if total is not None:
        # Folder runs are set up off-thread; the count is all this rule reads.
        tab._total_files = total
        tab._total_pairs = total
        tab._total_urls = total
    for slot in finished:
        slot(0, None, "exit status 1: something low-level")
    issue = tab.issue_banner().current_issue()
    assert issue is not None
    return tab, issue


def test_a_one_file_failure_is_singular(qtbot, tmp_path):
    _tab, issue = _fail_one(_CONDENSE, qtbot, tmp_path)

    assert issue.summary == "This file could not be condensed."
    assert issue.details == "exit status 1: something low-level"


def test_a_several_file_run_keeps_some(qtbot, tmp_path):
    _tab, issue = _fail_one(_CONDENSE, qtbot, tmp_path, total=3)

    assert issue.summary == "Some files could not be condensed."


def test_a_known_ffmpeg_fault_gets_its_own_sentence_and_no_repair(qtbot, tmp_path):
    _tab, issue = _fail_one(_CONDENSE, qtbot, tmp_path, fatal=FilterUnavailableError("x"))

    assert issue.summary == "This ffmpeg build cannot condense audio. Install a different ffmpeg build."
    assert issue.action_text == ""


def test_retime_offers_alass_only_while_it_is_missing(qtbot, tmp_path):
    tab = _make_tab(_RETIME, qtbot, tmp_path)
    # Set, not probed: the probe's verdict lands on a queued callback.
    tab._alass_is_available = True
    _start_single_run(_RETIME, tab, tmp_path, FakeToolWorker(), qtbot)
    tab.log_widget.append_error("exit status 1: first")
    assert tab.issue_banner().current_issue().action_text == ""

    tab._alass_is_available = False
    tab.log_widget.append_error("exit status 1: again")
    issue = tab.issue_banner().current_issue()

    assert issue.summary == "This file could not be retimed."
    assert issue.action_id == "tools.retime.alass"
    assert issue.action_text == "Download alass"


def test_the_retime_repair_reveals_the_alass_download(qtbot, tmp_path):
    tab, _issue = _fail_one(_RETIME, qtbot, tmp_path)
    tab._alass_is_available = False
    revealed: list[str] = []
    tab._reveal_setting = revealed.append  # type: ignore[method-assign]
    tab.log_widget.append_error("exit status 1: again")

    tab.issue_banner().action_button.click()

    assert revealed == ["subtitles.alass_download"]


def test_download_names_a_missing_ytdlp_and_offers_the_update(qtbot, tmp_path):
    tab, issue = _fail_one(_DOWNLOAD, qtbot, tmp_path, fatal=YtdlpNotFoundError("no yt-dlp"))
    revealed: list[str] = []
    tab._reveal_setting = revealed.append  # type: ignore[method-assign]

    assert issue.summary == "yt-dlp is not installed, so downloads cannot run."
    assert issue.action_text == "Update yt-dlp"
    tab.issue_banner().action_button.click()
    assert revealed == ["youtube.ytdlp_update"]


@pytest.mark.parametrize("spec", [_CONDENSE, _RETIME, _DOWNLOAD], ids=lambda s: s.tab_cls.__name__)
def test_every_tool_has_a_singular_sentence(spec, qtbot, tmp_path):
    tab = _make_tab(spec, qtbot, tmp_path)

    assert tab._strings.run_problem_single
    assert tab._strings.run_problem_single != tab._strings.run_problem

"""ReadabilityTab (Utilities → Readability, #132).

Same harness as tests/unit/test_booksync_tab.py: the engine probe and the
worker class are patched at the tab's import site. The shared
``_ToolTabBase`` run lifecycle is tested once in ``test_tool_tab_contract.py``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.widgets.readability_tab import ReadabilityTab
from anki_miner.models import TerminalOutcome
from anki_miner.models.readability import ReadabilityStats
from tests.unit._tool_tab_harness import FakeToolWorker, capture_slots
from tests.unit._tool_tab_harness import make_config as _make_config

_PROBE = "anki_miner.gui.widgets.readability_tab.ReadabilityTab._compute_engine_available"
_WORKER_CLS = "anki_miner.gui.widgets.readability_tab.ReadabilityWorker"


def _make_tab(config, qtbot, *, available: bool = True) -> ReadabilityTab:
    with patch(_PROBE, return_value=available):
        tab = ReadabilityTab(config)
        qtbot.addWidget(tab)
        assert tab._availability_worker.wait(3000)
        if available:
            qtbot.waitUntil(tab.check_button.isEnabled, timeout=3000)
        else:
            qtbot.waitUntil(lambda: not tab.engine_notice_label.isHidden(), timeout=3000)
    return tab


def _stats(words: int, unknown: int, new: set[str], buckets: tuple[int, int, int]) -> ReadabilityStats:
    return ReadabilityStats(words, unknown, frozenset(new), buckets)


def _start(tab: ReadabilityTab, path: Path) -> tuple[FakeToolWorker, list, list, list]:
    """Click Check on *path* (a file); return the worker and its measured / error / finished slots."""
    worker = FakeToolWorker()
    measured = capture_slots(worker.file_measured)
    errors = capture_slots(worker.error)
    finished = capture_slots(worker.queue_finished)
    tab.input_selector.set_path(str(path))
    with patch(_WORKER_CLS, return_value=worker):
        tab.check_button.click()
    return worker, measured, errors, finished


def _subtitle(tmp_path: Path, name: str = "episode.srt") -> Path:
    sub = tmp_path / name
    sub.write_text("1\n00:00:00,000 --> 00:00:01,000\n猫\n", encoding="utf-8")
    return sub


def _rows(tab: ReadabilityTab) -> list[tuple[str, ...]]:
    """(File, Known, New words) per row, top to bottom."""
    table = tab.files_table
    return [tuple(table.item(r, c).text() for c in range(3)) for r in range(table.rowCount())]


def test_identity(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.TASK_ID == "tools.readability"
    assert (tab.TASK_OWNER.main_tab, tab.TASK_OWNER.subtab) == ("subtitles", "readability")
    assert tab.OUTPUT_HISTORY_KEY == ""
    assert tab.input_selector._history_key == "tools.readability.inputs"
    assert tab.report_card.isHidden()


def test_engine_unavailable_disables_check(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot, available=False)
    assert not tab.check_button.isEnabled()
    assert not tab.engine_notice_label.isHidden()


@pytest.mark.parametrize("kind", ["empty", "missing", "not_subtitle"])
def test_refusals_start_nothing(kind, qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    if kind == "missing":
        tab.input_selector.set_path(str(tmp_path / "gone.srt"))
    elif kind == "not_subtitle":
        notes = tmp_path / "notes.txt"
        notes.write_text("x")
        tab.input_selector.set_path(str(notes))
    with patch(_WORKER_CLS) as worker_cls:
        tab._on_check()
    assert tab.issue_banner().current_issue() is not None
    worker_cls.assert_not_called()


def test_single_file_starts_worker(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    sub = _subtitle(tmp_path)
    worker = FakeToolWorker()
    tab.input_selector.set_path(str(sub))
    with patch(_WORKER_CLS, return_value=worker) as worker_cls:
        tab.check_button.click()
    assert worker_cls.call_args.args == (tab.config, [sub])
    assert worker._started


def test_folder_scans_subtitles_in_natural_order(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    folder = tmp_path / "season"
    folder.mkdir()
    for name in ("10.srt", "2.srt", "1.ass", "notes.txt", "._1.srt"):
        (folder / name).write_text("x")
    tab.input_selector.set_path(str(folder))
    with patch(_WORKER_CLS, return_value=FakeToolWorker()) as worker_cls:
        tab.check_button.click()
        qtbot.waitUntil(lambda: worker_cls.called, timeout=3000)
    assert [p.name for p in worker_cls.call_args.args[1]] == ["1.ass", "2.srt", "10.srt"]


@pytest.mark.parametrize(
    ("language", "names", "kept"),
    [
        (
            "ja",
            ("ep01.ja.srt", "ep01.en.srt", "ep01.eng.forced.srt", "ep01.zh.srt", "ep02.srt", "Show.All.In.srt"),
            {"ep01.ja.srt", "ep02.srt", "Show.All.In.srt"},  # "In" is a title word, not Indonesian
        ),
        ("es", ("ep01.es.srt", "ep01.en.srt", "ep01.pt-BR.srt", "ep01.spa.srt"), {"ep01.es.srt", "ep01.spa.srt"}),
    ],
)
def test_folder_leaves_out_subtitles_tagged_for_another_language(language, names, kept, qtbot, tmp_path):
    """Review Focus 2: ``ep01.en.srt`` beside ``ep01.es.srt`` would pass a Latin script gate and skew the totals."""
    tab = _make_tab(replace(_make_config(tmp_path), language=language), qtbot)
    folder = tmp_path / "season"
    folder.mkdir()
    for name in names:
        (folder / name).write_text("x")
    tab.input_selector.set_path(str(folder))
    with patch(_WORKER_CLS, return_value=FakeToolWorker()) as worker_cls:
        tab.check_button.click()
        qtbot.waitUntil(lambda: worker_cls.called, timeout=3000)
    assert {p.name for p in worker_cls.call_args.args[1]} == kept


def test_a_single_file_is_checked_whatever_its_language_tag(qtbot, tmp_path):
    """Picking one file is an explicit choice; only the folder scan sorts by tag."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    sub = _subtitle(tmp_path, "ep01.en.srt")
    tab.input_selector.set_path(str(sub))
    with patch(_WORKER_CLS, return_value=FakeToolWorker()) as worker_cls:
        tab.check_button.click()
    assert worker_cls.call_args.args[1] == [sub]


def test_empty_folder_banners_and_rearms(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    folder = tmp_path / "empty"
    folder.mkdir()
    tab.input_selector.set_path(str(folder))
    with patch(_WORKER_CLS) as worker_cls:
        tab.check_button.click()
        qtbot.waitUntil(lambda: tab.issue_banner().current_issue() is not None, timeout=3000)
    worker_cls.assert_not_called()
    assert tab.check_button.isEnabled()


def test_measured_rows_fill_table_and_totals(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _worker, measured, _errors, _finished = _start(tab, _subtitle(tmp_path))
    tab._run_files = [tmp_path / "1.srt", tmp_path / "2.srt"]

    measured[0](0, _stats(100, 10, {"犬"}, (8, 1, 1)))
    measured[0](1, _stats(10, 10, {"犬", "鳥"}, (0, 0, 2)))

    assert tab.files_table.rowCount() == 2
    assert tab.known_card.value_label.text() == "81.8%"  # 90 of 110 occurrences, not mean(90, 0)
    assert tab.new_words_card.value_label.text() == "2"  # 犬 counted once
    assert tab.i0_card.value_label.text() == "67%"  # 8 of 12 lines
    assert tab.i1_card.value_label.text() == "8%"
    assert not tab.report_card.isHidden()


def test_totals_never_revisit_earlier_files(qtbot, tmp_path, monkeypatch):
    """Per-row work must not grow with the folder (Readability review, minor 4: 500+ episodes)."""
    from anki_miner.gui.widgets import readability_tab
    from anki_miner.services.readability import combine

    sizes: list[int] = []

    def spy(stats):
        stats = list(stats)
        sizes.append(len(stats))
        return combine(stats)

    monkeypatch.setattr(readability_tab, "combine", spy)
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _worker, measured, _errors, _finished = _start(tab, _subtitle(tmp_path))
    tab._run_files = [tmp_path / f"{i}.srt" for i in range(5)]

    for idx in range(5):
        measured[0](idx, _stats(10, idx, {f"w{idx}", "犬"}, (1, 0, 0)))

    assert max(sizes) <= 2
    assert tab.new_words_card.value_label.text() == "6"  # w0..w4 and 犬 once
    assert tab.known_card.value_label.text() == "80.0%"  # 40 of 50 occurrences


def test_sorted_insert_keeps_cells_together(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _worker, measured, _errors, _finished = _start(tab, _subtitle(tmp_path))
    tab._run_files = [tmp_path / "a.srt", tmp_path / "b.srt", tmp_path / "c.srt"]

    measured[0](0, _stats(100, 50, {"犬"}, (1, 0, 0)))
    measured[0](1, _stats(100, 10, {"犬", "鳥"}, (1, 0, 0)))
    tab.files_table.sortItems(1, Qt.SortOrder.DescendingOrder)
    # Lands between the two: Qt moves the row when its Known cell is set.
    measured[0](2, _stats(100, 30, {"犬", "鳥", "魚"}, (1, 0, 0)))

    assert _rows(tab) == [("b.srt", "90.0%", "2"), ("c.srt", "70.0%", "3"), ("a.srt", "50.0%", "1")]


def test_default_order_is_run_order(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _worker, measured, _errors, _finished = _start(tab, _subtitle(tmp_path))
    tab._run_files = [tmp_path / "1.srt", tmp_path / "2.srt", tmp_path / "10.srt"]

    for idx in range(3):
        measured[0](idx, _stats(100, 10 * idx, {"犬"}, (1, 0, 0)))

    assert [row[0] for row in _rows(tab)] == ["1.srt", "2.srt", "10.srt"]


def test_rerun_clears_report(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    sub = _subtitle(tmp_path)
    worker, measured, _errors, finished = _start(tab, sub)
    measured[0](0, _stats(100, 10, {"犬"}, (1, 0, 0)))
    finished[0](TerminalOutcome.SUCCESS)
    tab.worker_thread = None  # the QThread has exited

    _start(tab, sub)

    assert tab.files_table.rowCount() == 0
    assert tab.known_card.value_label.text() == "—"
    assert tab.report_card.isHidden()


def test_a_refusal_clears_the_last_report(qtbot, tmp_path):
    """An old report must not sit under a new complaint (Readability review, minor 7)."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _worker, measured, _errors, finished = _start(tab, _subtitle(tmp_path))
    measured[0](0, _stats(100, 10, {"犬"}, (1, 0, 0)))
    finished[0](TerminalOutcome.SUCCESS)
    tab.worker_thread = None  # the QThread has exited

    tab.input_selector.set_path(str(tmp_path / "gone.srt"))
    tab.check_button.click()

    assert tab.issue_banner().current_issue() is not None
    assert tab.files_table.rowCount() == 0
    assert tab.report_card.isHidden()


def test_cancel_keeps_measured_rows(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _worker, measured, _errors, finished = _start(tab, _subtitle(tmp_path))
    measured[0](0, _stats(100, 10, {"犬"}, (1, 0, 0)))

    tab._on_cancel()
    finished[0](TerminalOutcome.CANCELLED)

    assert tab.files_table.rowCount() == 1
    assert not tab.report_card.isHidden()


def test_anki_unreachable_names_anki_in_the_banner(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    worker, _measured, errors, _finished = _start(tab, _subtitle(tmp_path))
    worker.fatal_exception = AnkiConnectionError("refused")

    errors[0]("refused")

    issue = tab.issue_banner().current_issue()
    assert issue is not None
    assert "Anki" in issue.summary
    assert issue.details == "refused"


def test_status_says_what_happens_before_the_first_file(qtbot, tmp_path):
    """Building dictionaries and reading Anki can take seconds (Readability review, minor 5)."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _start(tab, _subtitle(tmp_path))
    assert tab.progress_widget.status_label.text() == "Reading your Anki cards and known words…"


def test_load_warnings_land_in_the_log(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    worker = FakeToolWorker()
    slots = capture_slots(worker.load_warning)
    tab.input_selector.set_path(str(_subtitle(tmp_path)))
    with patch(_WORKER_CLS, return_value=worker):
        tab.check_button.click()

    slots[0]("Couldn't load frequency data: gone")

    assert "Couldn't load frequency data: gone" in tab.log_widget.full_text()


def test_probe_mid_run_does_not_rearm_check(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _start(tab, _subtitle(tmp_path))
    tab._apply_probe_result(True)
    assert not tab.check_button.isEnabled()


def test_every_header_and_card_has_a_tooltip(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    table = tab.files_table
    assert table.columnCount() == 6
    for col in range(table.columnCount()):
        assert table.horizontalHeaderItem(col).toolTip(), col
    for card in (tab.known_card, tab.new_words_card, tab.i0_card, tab.i1_card):
        assert card.toolTip()

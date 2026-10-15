"""WP-34 (ruling R10): the run results window exports mined and not-mined words with reasons."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

import anki_miner.gui.widgets.dialogs.results_dialog as results_mod
from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog
from anki_miner.models.processing import NotMinedReason, NotMinedReport, ProcessingResult

_REPORT = NotMinedReport({NotMinedReason.KNOWN: frozenset({"猫"})}, mined=frozenset({"走る"}))


def _dialog(qtbot, report: NotMinedReport | None) -> ResultsDialog:
    result = ProcessingResult(total_words_found=3, new_words_found=2, cards_created=1, card_ids=[1], not_mined=report)
    dialog = ResultsDialog(result)
    qtbot.addWidget(dialog)
    return dialog


@pytest.fixture
def run_now(monkeypatch):
    """Run the off-thread work inline; 'fail' makes it report an error instead."""
    state = {"fail": ""}

    def fake(parent, work, on_done, on_error=None, **kwargs):
        if state["fail"]:
            on_error(state["fail"])
            return None
        on_done(work())
        return None

    monkeypatch.setattr(results_mod, "run_off_thread", fake)
    return state


def _pick(monkeypatch, path) -> dict[str, str]:
    seen: dict[str, str] = {}

    def pick(parent, title, initial, file_filter, *args, on_done, **kwargs):
        seen["initial"] = initial
        seen["filter"] = file_filter
        on_done(str(path) if path else "")

    monkeypatch.setattr(results_mod.file_dialogs, "pick_save_file", pick)
    return seen


def test_no_report_no_button(qtbot):
    assert _dialog(qtbot, None)._export_button is None


def test_an_empty_report_no_button(qtbot):
    assert _dialog(qtbot, NotMinedReport())._export_button is None


def test_export_writes_the_run_words(qtbot, tmp_path, monkeypatch, run_now):
    dialog = _dialog(qtbot, _REPORT)
    out = tmp_path / "words.csv"
    seen = _pick(monkeypatch, out)

    assert dialog._export_button is not None
    assert dialog._export_button.text() == "Export words…"
    dialog._export_button.click()

    assert out.read_text(encoding="utf-8-sig").splitlines() == [
        "word,status,reason",
        "走る,mined,",
        "猫,not_mined,known",
    ]
    assert dialog._export_button.text() == "Words exported"
    assert dialog._export_button.isEnabled()
    assert seen["initial"].endswith("words.csv")
    assert "*.csv" in seen["filter"]


def test_a_cancelled_picker_writes_nothing(qtbot, tmp_path, monkeypatch, run_now):
    dialog = _dialog(qtbot, _REPORT)
    _pick(monkeypatch, None)

    dialog._export_button.click()

    assert dialog._export_button.text() == "Export words…"
    assert list(tmp_path.iterdir()) == []


def test_a_failed_export_says_so_on_the_button_without_a_modal(qtbot, tmp_path, monkeypatch, run_now, caplog):
    dialog = _dialog(qtbot, _REPORT)
    _pick(monkeypatch, tmp_path / "words.csv")
    run_now["fail"] = "disk full"

    dialog._export_button.click()

    assert dialog._export_button.text() == "Export failed — try again"
    assert dialog._export_button.isEnabled()
    assert "disk full" in dialog._export_button.toolTip()
    assert any(r.getMessage().startswith("Run words export failed:") for r in caplog.records)

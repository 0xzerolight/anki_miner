"""Curator lifecycle logging: shown, decision, skipped.

"Review words was on and the run stopped after item 1" is the single most
common curator report, and the log had nothing to say about it: rejecting the
curator cancels the WHOLE run (``_resolve_curation`` calls ``worker.cancel()``),
while confirming with nothing selected skips just that item. Without a record of
which verb the user used, that report is unreproducible.

These tests pin the three anchors and the fields that make them diagnostic, plus
the four curator-adjacent handlers that used to discard ``str(exc)``.
"""

from __future__ import annotations

import logging
from unittest.mock import Mock, patch

import pytest
from PyQt6.QtWidgets import QDialog

from anki_miner.exceptions import AnkiMinerException
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase

MODULE = "anki_miner.gui.widgets._mining_tab_base"


class _Bare(MiningTabBase):
    TASK_ID = "screen.test"
    config = None

    def _commit_known_words(self, forms):
        return 0

    def _restore_buttons(self) -> None:
        pass


def _fake_dialog_cls():
    created: list = []

    class _FakeCurationDialog(QDialog):
        def __init__(self, words, parent=None, **kwargs):
            super().__init__(parent)
            self.words = list(words)
            self.selection: list = ["picked"]
            created.append(self)

        def get_selected_words(self):
            return self.selection

    return _FakeCurationDialog, created


@pytest.fixture
def tab(qapp, qtbot):
    widget = _Bare()
    qtbot.addWidget(widget)
    widget._init_curation_bridge()
    return widget


def _show(tab, words=("w1", "w2"), media=None, lookup=None, token=None):
    cls, created = _fake_dialog_cls()
    with patch(f"{MODULE}.WordCurationDialog", cls):
        tab._show_curation_dialog(list(words), media, lookup, token)
    return created[0] if created else None


def _messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def _one(caplog, anchor: str) -> str:
    hits = [m for m in _messages(caplog) if m.startswith(anchor)]
    assert len(hits) == 1, f"expected exactly one {anchor!r}, got {_messages(caplog)}"
    return hits[0]


# ---------------------------------------------------------------------------
# Curator shown
# ---------------------------------------------------------------------------


def test_showing_the_curator_logs_an_inventory(tab, caplog):
    with caplog.at_level(logging.INFO, logger=MODULE):
        dialog = _show(tab, words=("食べる", "走る", "見る"), media=object(), lookup=lambda w: [])

    line = _one(caplog, "Curator shown:")
    assert "screen=screen.test" in line
    assert "words=3" in line
    assert "media=True" in line
    assert "lookup=True" in line
    assert f"presentation={tab._curation_dialog_seq}" in line
    dialog.deleteLater()


def test_table_only_curator_records_missing_media_and_lookup(tab, caplog):
    with caplog.at_level(logging.INFO, logger=MODULE):
        dialog = _show(tab)

    line = _one(caplog, "Curator shown:")
    assert "media=False" in line
    assert "lookup=False" in line
    dialog.deleteLater()


# ---------------------------------------------------------------------------
# Curator decision
# ---------------------------------------------------------------------------


def test_reject_logs_a_run_cancelling_decision(tab, caplog):
    tab.worker_thread = Mock()
    with caplog.at_level(logging.INFO, logger=MODULE):
        tab._curation_pending_dialog = 7
        tab._resolve_curation(None, 7, QDialog.DialogCode.Rejected)

    line = _one(caplog, "Curator decision:")
    assert "screen=screen.test" in line
    assert "action=reject" in line
    assert "selected=-" in line
    assert "cancels_run=True" in line
    assert "presentation=7" in line


def test_accept_logs_the_selected_count_and_does_not_cancel(tab, caplog):
    tab.worker_thread = Mock()
    dialog = _show(tab, words=("a", "b", "c"))
    dialog.selection = ["a", "b"]

    with caplog.at_level(logging.INFO, logger=MODULE):
        dialog.accept()

    line = _one(caplog, "Curator decision:")
    assert "action=accept" in line
    assert "selected=2" in line
    assert "offered=3" in line
    assert "cancels_run=False" in line


def test_confirm_with_nothing_selected_is_an_accept_not_a_cancel(tab, caplog):
    """`[] != None`: an empty confirm skips one item; only None stops the run."""
    tab.worker_thread = Mock()
    dialog = _show(tab, words=("a",))
    dialog.selection = []

    with caplog.at_level(logging.INFO, logger=MODULE):
        dialog.accept()

    line = _one(caplog, "Curator decision:")
    assert "action=accept" in line
    assert "selected=0" in line
    assert "cancels_run=False" in line


# ---------------------------------------------------------------------------
# Curator skipped
# ---------------------------------------------------------------------------


def test_stale_token_build_is_recorded_as_skipped(tab, caplog):
    tab._curation_live_token = 9
    with caplog.at_level(logging.DEBUG, logger=MODULE):
        assert _show(tab, token=3) is None

    line = _one(caplog, "Curator skipped:")
    assert "reason=stale_token" in line
    assert "screen=screen.test" in line


def test_cancel_before_presentation_is_recorded_as_skipped(tab, caplog):
    tab._curation_cancelled = True
    with caplog.at_level(logging.DEBUG, logger=MODULE):
        assert _show(tab) is None

    line = _one(caplog, "Curator skipped:")
    assert "reason=cancelled" in line
    assert tab._curation_event.is_set()


def test_poisoned_gate_before_the_slot_runs_is_recorded_as_skipped(tab, caplog):
    tab._curation_gate_poisoned = True
    with caplog.at_level(logging.DEBUG, logger=MODULE):
        tab._on_curation_requested(["w1"])

    line = _one(caplog, "Curator skipped:")
    assert "reason=gate_poisoned" in line


def test_second_resolution_of_one_presentation_is_recorded_as_skipped(tab, caplog):
    """`finished` then `destroyed` both fire; only the first decides."""
    tab.worker_thread = Mock()
    tab._curation_pending_dialog = 4
    tab._resolve_curation(None, 4, QDialog.DialogCode.Rejected)

    with caplog.at_level(logging.DEBUG, logger=MODULE):
        tab._resolve_curation(None, 4, QDialog.DialogCode.Rejected)

    line = _one(caplog, "Curator skipped:")
    assert "reason=already_resolved" in line
    assert not any(m.startswith("Curator decision:") for m in _messages(caplog))


# ---------------------------------------------------------------------------
# The four handlers that used to discard str(exc)
# ---------------------------------------------------------------------------


def test_selection_failure_keeps_the_exception_message_and_stack(tab, caplog):
    tab.worker_thread = Mock()
    dialog = _show(tab)
    dialog.get_selected_words = Mock(side_effect=ValueError("table model gone"))

    with caplog.at_level(logging.WARNING, logger=MODULE):
        dialog.accept()

    record = next(r for r in caplog.records if "Curation selection unavailable" in r.getMessage())
    assert "ValueError" in record.getMessage()
    assert "table model gone" in record.getMessage()
    assert record.exc_info is not None


def test_typed_failure_keeps_its_message_but_drops_the_traceback(tab, caplog):
    tab.worker_thread = Mock()
    dialog = _show(tab)
    dialog.get_selected_words = Mock(side_effect=AnkiMinerException("no words left"))

    with caplog.at_level(logging.WARNING, logger=MODULE):
        dialog.accept()

    record = next(r for r in caplog.records if "Curation selection unavailable" in r.getMessage())
    assert "no words left" in record.getMessage()
    assert record.exc_info is None


def test_curation_media_failure_keeps_the_exception_message(test_config, caplog, tmp_path):
    video = tmp_path / "ep.mkv"
    subtitle = tmp_path / "ep.srt"
    video.touch()
    subtitle.touch()

    with (
        patch(f"{MODULE}.SubtitleParserService", side_effect=OSError("index gone")),
        caplog.at_level(logging.WARNING, logger=MODULE),
    ):
        assert MiningTabBase._make_curation_media_context(test_config, video, subtitle, 0.0) is None

    record = next(r for r in caplog.records if "Curation media unavailable" in r.getMessage())
    assert "OSError" in record.getMessage()
    assert "index gone" in record.getMessage()
    assert record.exc_info is not None


def test_secondary_subtitle_failure_keeps_the_exception_message(test_config, caplog, tmp_path):
    video = tmp_path / "ep.mkv"
    subtitle = tmp_path / "ep.srt"
    second = tmp_path / "ep.en.srt"
    for path in (video, subtitle, second):
        path.touch()

    parser = Mock()
    parser.parse_raw_entries.side_effect = [[], UnicodeDecodeError("utf-8", b"", 0, 1, "bad")]

    with (
        patch(f"{MODULE}.SubtitleParserService", return_value=parser),
        caplog.at_level(logging.WARNING, logger=MODULE),
    ):
        MiningTabBase._make_curation_media_context(test_config, video, subtitle, 0.0, secondary_subtitle=second)

    record = next(r for r in caplog.records if "Secondary subtitle unavailable" in r.getMessage())
    assert "UnicodeDecodeError" in record.getMessage()
    assert "bad" in record.getMessage()
    assert record.exc_info is not None


def test_run_details_failure_keeps_the_exception_message(tab, caplog):
    widget = Mock()
    widget.receipt.aggregate_result.return_value = object()
    tab._receipt_widget = widget
    tab._presenter = Mock()
    tab._presenter.show_run_details.side_effect = RuntimeError("receipt gone")

    with caplog.at_level(logging.WARNING, logger=MODULE):
        tab._open_run_details()

    record = next(r for r in caplog.records if "Run details unavailable" in r.getMessage())
    assert "RuntimeError" in record.getMessage()
    assert "receipt gone" in record.getMessage()
    assert record.exc_info is not None

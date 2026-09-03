"""Logging contract for the app-wide file-picker wrappers.

Every picker outcome is one INFO receipt: which caption asked, who owned the
dialog, whether the user accepted / cancelled / was silenced by a closing
parent, and what came back. "Which file did I pick?" is otherwise unanswerable
from a log — the picker is non-blocking and its continuation logs nothing.

The dialog is a stub, as in ``test_file_dialogs.py``: a real ``QFileDialog``
would enumerate the filesystem for a test that only cares about the receipt.
"""

from __future__ import annotations

import logging

import pytest
from PyQt6.QtWidgets import QDialog, QWidget

from anki_miner.gui.utils import file_dialogs as fd

LOGGER_NAME = "anki_miner.gui.utils.file_dialogs"


class _FakeDialog:
    """The slice of ``QFileDialog`` that ``_launch``/``_finish`` touch."""

    def __init__(self) -> None:
        self.selection: list[str] = []
        self.deleted = False
        self._finished_slots: list = []

    def selectedFiles(self):  # noqa: N802 — Qt API name
        return list(self.selection)

    def setAttribute(self, attr, on):  # noqa: N802 — Qt API name
        return None

    def deleteLater(self):  # noqa: N802 — Qt API name
        self.deleted = True

    def open(self):
        return None

    def reject(self):
        self.fire(QDialog.DialogCode.Rejected)

    @property
    def finished(self):
        return self

    def connect(self, slot):
        self._finished_slots.append(slot)

    def fire(self, code):
        for slot in list(self._finished_slots):
            slot(int(code))


@pytest.fixture
def live(monkeypatch):
    """Isolate the module-global live-picker registry."""
    registry: list = []
    monkeypatch.setattr(fd, "_live", registry)
    return registry


def _entry(*, empty="", parent=None, caption="Choose subtitle"):
    return fd._Picker(_FakeDialog(), parent, lambda _v: None, empty, caption)


def _messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME]


def test_finish_logs_accepted_outcome_with_value(live, caplog):
    entry = _entry()
    entry.dialog.selection = ["/data/anime/ep01.srt"]

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        fd._finish(entry, int(QDialog.DialogCode.Accepted))

    assert _messages(caplog) == [
        'File picker: caption="Choose subtitle" parent=- outcome=accepted ' "value=/data/anime/ep01.srt"
    ]


def test_finish_logs_cancelled_outcome_with_parent(qtbot, live, caplog):
    parent = QWidget()
    qtbot.addWidget(parent)
    entry = _entry(parent=parent)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        fd._finish(entry, int(QDialog.DialogCode.Rejected))

    messages = _messages(caplog)
    assert len(messages) == 1
    assert "outcome=cancelled" in messages[0]
    assert "parent=QWidget" in messages[0]
    assert "value=-" in messages[0]


def test_finish_logs_silenced_outcome(live, caplog):
    entry = _entry()
    entry.silenced = True

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        fd._finish(entry, int(QDialog.DialogCode.Accepted))

    messages = _messages(caplog)
    assert len(messages) == 1
    assert "outcome=silenced" in messages[0]
    assert "value=-" in messages[0]


def test_finish_logs_every_picked_file_of_a_multi_select(live, caplog):
    entry = _entry(empty=[])
    entry.dialog.selection = ["/a/one.srt", "/a/two.srt"]

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        fd._finish(entry, int(QDialog.DialogCode.Accepted))

    messages = _messages(caplog)
    assert len(messages) == 1
    assert "value=/a/one.srt,/a/two.srt" in messages[0]


def test_finish_logs_once_even_when_the_parent_died(qtbot, live, caplog, monkeypatch):
    """A receipt is written for the result the continuation never sees."""
    parent = QWidget()
    qtbot.addWidget(parent)
    monkeypatch.setattr(fd, "widget_alive", lambda widget: widget is not parent)
    entry = _entry(parent=parent)
    entry.dialog.selection = ["/a/one.srt"]

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        fd._finish(entry, int(QDialog.DialogCode.Accepted))

    messages = _messages(caplog)
    assert len(messages) == 1
    assert "outcome=accepted" in messages[0]


def test_launch_carries_the_caption_into_the_receipt(live, caplog):
    dialog = _FakeDialog()
    dialog.selection = ["/a/one.srt"]
    fd._launch(dialog, None, lambda _v: None, "", caption="Pick a folder")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        dialog.fire(QDialog.DialogCode.Accepted)

    messages = _messages(caplog)
    assert len(messages) == 1
    assert 'caption="Pick a folder"' in messages[0]


def test_cancel_all_pickers_logs_the_live_count(live, caplog):
    live.append(_entry())
    live.append(_entry(caption="Other"))

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        fd.cancel_all_pickers()

    assert any("File picker cancel_all: count=2" in m for m in _messages(caplog))

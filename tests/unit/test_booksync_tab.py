"""BookSyncTab (Utilities → Audiobook Sync).

Same harness as tests/unit/test_subtitle_creation_tab.py: engine probe and
worker class patched at the tab's import site; no ffmpeg, no ASR.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.booksync_tab import BookSyncTab
from anki_miner.models import TerminalOutcome

_ENGINE_AVAILABLE = "anki_miner.services.asr._engine.available"
_USABLE_MODEL = "anki_miner.gui.widgets.booksync_tab.usable_model_installed"
_OS_ACCESS = "anki_miner.gui.widgets.booksync_tab.os.access"
_WORKER_CLS = "anki_miner.gui.widgets.booksync_tab.BookSyncWorker"


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(asr_models_root=tmp_path / "asr_models", media_temp_folder=tmp_path / "tmp")


class _FakeWorker:
    instances: list[_FakeWorker] = []

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        for name in (
            "file_started",
            "file_progress",
            "file_finished",
            "file_skipped",
            "queue_finished",
            "error",
            "finished",
        ):
            setattr(self, name, MagicMock())
        self.deleteLater = MagicMock()
        self._started = False
        self._cancelled = False
        _FakeWorker.instances.append(self)

    def start(self):
        self._started = True

    def cancel(self):
        self._cancelled = True

    def isRunning(self):
        return self._started and not self._cancelled

    def wait(self, *a):
        return True


def _make_tab(config, qtbot) -> BookSyncTab:
    with patch(_ENGINE_AVAILABLE, return_value=True):
        tab = BookSyncTab(config)
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(tab.sync_button.isEnabled, timeout=3000)
    qtbot.addWidget(tab)
    return tab


def _inputs(tmp_path: Path, *, folder: bool = False) -> tuple[Path, Path]:
    book = tmp_path / "neko.epub"
    book.write_bytes(b"PK")
    if folder:
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        for name in ("10.mp3", "2.mp3", "1.mp3", "notes.txt", "._1.mp3"):
            (audio_dir / name).write_bytes(b"x")
        return audio_dir, book
    audio = tmp_path / "neko.m4b"
    audio.write_bytes(b"x")
    return audio, book


@pytest.fixture(autouse=True)
def _reset_fake_worker():
    _FakeWorker.instances.clear()


def test_construction_and_identity(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.TASK_ID == "tools.booksync"
    assert tab.TASK_OWNER.subtab == "booksync"
    assert tab.OUTPUT_HISTORY_KEY == "tools.booksync.output"
    assert tab.file_selector._history_key == "tools.booksync.inputs"
    assert tab.book_selector._history_key == "tools.booksync.inputs"
    assert not tab.folder_selector.isVisibleTo(tab)


def test_engine_unavailable_disables_sync_and_shows_notice(qtbot, tmp_path):
    with patch(_ENGINE_AVAILABLE, return_value=False):
        tab = BookSyncTab(_make_config(tmp_path))
        qtbot.addWidget(tab)
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(lambda: not tab.engine_notice_label.isHidden(), timeout=3000)
    assert not tab.sync_button.isEnabled()


def test_mode_toggle_swaps_selectors(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab._on_folder_mode()
    assert tab.file_selector.isHidden() and not tab.folder_selector.isHidden()
    tab._on_file_mode()
    assert not tab.file_selector.isHidden() and tab.folder_selector.isHidden()


def test_sync_refuses_without_a_book(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    audio, _ = _inputs(tmp_path)
    tab.file_selector.set_path(str(audio))
    with patch(_WORKER_CLS, _FakeWorker):
        tab._on_sync()
    assert _FakeWorker.instances == []
    assert tab.issue_banner().current_issue() is not None


def test_sync_refuses_a_non_book_file(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    audio, _ = _inputs(tmp_path)
    tab.file_selector.set_path(str(audio))
    wrong = tmp_path / "x.srt"
    wrong.write_text("1", encoding="utf-8")
    tab.book_selector.set_path(str(wrong))
    with patch(_WORKER_CLS, _FakeWorker):
        tab._on_sync()
    assert _FakeWorker.instances == []
    assert tab.issue_banner().current_issue() is not None


def test_single_file_starts_worker_with_audio_book_and_options(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    audio, book = _inputs(tmp_path)
    tab.file_selector.set_path(str(audio))
    tab.book_selector.set_path(str(book))
    tab.overwrite_checkbox.setChecked(True)
    with (
        patch(_WORKER_CLS, _FakeWorker),
        patch(_USABLE_MODEL, return_value=True),
        patch(_ENGINE_AVAILABLE, return_value=True),
    ):
        tab._on_sync()
    worker = _FakeWorker.instances[0]
    assert worker.args[1] == [audio] and worker.args[2] == book
    assert worker.kwargs == {"output_dir": None, "overwrite": True}
    assert worker._started and tab.worker_thread is worker
    assert not tab.sync_button.isEnabled() and not tab.cancel_button.isHidden()
    assert tab._item_total() == 1


def test_folder_mode_natural_sorts_and_filters(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    audio_dir, book = _inputs(tmp_path, folder=True)
    tab._on_folder_mode()
    tab.folder_selector.set_path(str(audio_dir))
    tab.book_selector.set_path(str(book))
    with (
        patch(_WORKER_CLS, _FakeWorker),
        patch(_USABLE_MODEL, return_value=True),
        patch(_ENGINE_AVAILABLE, return_value=True),
    ):
        tab._on_sync()
        qtbot.waitUntil(lambda: bool(_FakeWorker.instances), timeout=3000)
    worker = _FakeWorker.instances[0]
    assert [p.name for p in worker.args[1]] == ["1.mp3", "2.mp3", "10.mp3"]
    assert tab._item_total() == 3


def test_model_not_ready_shows_banner_and_does_not_start(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    audio, book = _inputs(tmp_path)
    tab.file_selector.set_path(str(audio))
    tab.book_selector.set_path(str(book))
    with (
        patch(_WORKER_CLS, _FakeWorker),
        patch(_USABLE_MODEL, return_value=False),
        patch(_ENGINE_AVAILABLE, return_value=True),
    ):
        tab._on_sync()
    assert _FakeWorker.instances == []
    assert tab.sync_button.isEnabled()
    issue = tab.issue_banner().current_issue()
    assert issue is not None and issue.action_text == "Open Transcription Settings"


def test_unwritable_output_aborts(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    audio, book = _inputs(tmp_path)
    tab.file_selector.set_path(str(audio))
    tab.book_selector.set_path(str(book))
    with (
        patch(_WORKER_CLS, _FakeWorker),
        patch(_OS_ACCESS, return_value=False),
        patch(_ENGINE_AVAILABLE, return_value=True),
    ):
        tab._on_sync()
    assert _FakeWorker.instances == []
    assert tab.sync_button.isEnabled()


def test_queue_finished_reenables_and_reports(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    audio, book = _inputs(tmp_path)
    tab.file_selector.set_path(str(audio))
    tab.book_selector.set_path(str(book))
    with (
        patch(_WORKER_CLS, _FakeWorker),
        patch(_USABLE_MODEL, return_value=True),
        patch(_ENGINE_AVAILABLE, return_value=True),
    ):
        tab._on_sync()
    tab._on_file_started(0)
    tab._on_file_progress(0, 50, "Transcribing: 50%")
    tab._on_file_finished(0, tmp_path / "neko.srt", None)
    tab._on_queue_finished(TerminalOutcome.SUCCESS)
    assert tab.sync_button.isEnabled() and tab.cancel_button.isHidden()
    assert "neko.srt" in tab.log_widget.text_edit.toPlainText()


def test_cancel_forwards_to_worker(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    audio, book = _inputs(tmp_path)
    tab.file_selector.set_path(str(audio))
    tab.book_selector.set_path(str(book))
    with (
        patch(_WORKER_CLS, _FakeWorker),
        patch(_USABLE_MODEL, return_value=True),
        patch(_ENGINE_AVAILABLE, return_value=True),
    ):
        tab._on_sync()
    tab._on_cancel()
    assert _FakeWorker.instances[0]._cancelled
    assert not tab.cancel_button.isEnabled()


def test_iter_close_workers_yields_active_worker(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    audio, book = _inputs(tmp_path)
    tab.file_selector.set_path(str(audio))
    tab.book_selector.set_path(str(book))
    with (
        patch(_WORKER_CLS, _FakeWorker),
        patch(_USABLE_MODEL, return_value=True),
        patch(_ENGINE_AVAILABLE, return_value=True),
    ):
        tab._on_sync()
    assert list(tab.iter_close_workers()) == [tab.worker_thread]


def test_update_config_adopts_new_model(qtbot, tmp_path):
    import dataclasses

    config = _make_config(tmp_path)
    tab = _make_tab(config, qtbot)
    with patch(_ENGINE_AVAILABLE, return_value=True):
        tab.update_config(dataclasses.replace(config, asr_model="small"))
        assert tab._availability_worker.wait(3000)
    assert tab.config.asr_model == "small"

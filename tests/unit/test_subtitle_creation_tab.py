"""Tests for SubtitleCreationTab.

Covers:
- Construction (qtbot.addWidget contract)
- Mode toggle (single-file vs folder selector)
- Engine-unavailable disables Generate + shows notice
- Output-dir not writable aborts before starting worker
- Generate with stubbed worker drives ProgressWidget/LogWidget and re-enables Generate
- iter_close_workers returns the active worker
- ASR smoke handler (BUNDLED_SMOKE_PASS path)

No real ASR/ffmpeg runs: SubtitleGenWorker and _engine.available are monkeypatched.

Note on _engine.available patching:
  The engine patch at construction time enables the Generate button.  But
  _on_generate() also calls _engine.available() at click time.  Tests that
  click the button must therefore keep the engine patch active across the click
  as well (both contexts are needed).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab

# ---------------------------------------------------------------------------
# Common patch target constants
# ---------------------------------------------------------------------------

_ENGINE_AVAILABLE = "anki_miner.services.asr._engine.available"
_IS_DOWNLOADED = "anki_miner.gui.widgets.subtitle_creation_tab.model_manager.is_downloaded"
_OS_ACCESS = "anki_miner.gui.widgets.subtitle_creation_tab.os.access"
_WORKER_CLS = "anki_miner.gui.widgets.subtitle_creation_tab.SubtitleGenWorker"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    """Return a minimal config with writable paths under tmp_path."""
    return AnkiMinerConfig(
        asr_models_root=tmp_path / "asr_models",
        media_temp_folder=tmp_path / "tmp",
    )


class _FakeWorker:
    """Minimal fake that mimics the SubtitleGenWorker interface used by the tab."""

    def __init__(self, *args, **kwargs):
        # Per-instance mocks so connect() calls on different instances stay independent.
        self.file_started = MagicMock()
        self.file_progress = MagicMock()
        self.file_finished = MagicMock()
        self.queue_finished = MagicMock()
        self._started = False
        self._cancelled = False

    def start(self):
        self._started = True

    def cancel(self):
        self._cancelled = True

    def isRunning(self):
        return self._started and not self._cancelled

    def wait(self, *args):
        return True


def _make_tab(config, qtbot):
    """Construct a SubtitleCreationTab with engine patched available=True."""
    with patch(_ENGINE_AVAILABLE, return_value=True):
        tab = SubtitleCreationTab(config)
    qtbot.addWidget(tab)
    return tab


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction(qtbot, tmp_path):
    """Tab constructs and registers with qtbot without error."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab is not None


def test_generate_button_exists(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.generate_button is not None


def test_language_label_shows_japanese(qtbot, tmp_path):
    """Read-only Language: Japanese label must be present."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert "Japanese" in tab.language_label.text()


# ---------------------------------------------------------------------------
# Mode toggle
# ---------------------------------------------------------------------------


def test_mode_toggle_shows_file_selector_by_default(qtbot, tmp_path):
    """Single-file mode is the default; folder selector is explicitly hidden."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    # isVisible() is False for un-shown top-level; use isHidden() for explicit hide state.
    assert not tab.file_selector.isHidden()
    assert tab.folder_selector.isHidden()


def test_mode_toggle_switches_to_folder(qtbot, tmp_path):
    """Clicking folder mode button switches to folder selector."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.folder_mode_button.click()
    assert tab.file_selector.isHidden()
    assert not tab.folder_selector.isHidden()


def test_mode_toggle_back_to_file(qtbot, tmp_path):
    """Toggling back to file mode re-shows file selector, hides folder selector."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.folder_mode_button.click()
    tab.file_mode_button.click()
    assert not tab.file_selector.isHidden()
    assert tab.folder_selector.isHidden()


# ---------------------------------------------------------------------------
# Engine-unavailable guard
# ---------------------------------------------------------------------------


def test_engine_unavailable_disables_generate(qtbot, tmp_path):
    """When _engine.available() is False, Generate button must be disabled."""
    config = _make_config(tmp_path)
    with patch(_ENGINE_AVAILABLE, return_value=False):
        tab = SubtitleCreationTab(config)
    qtbot.addWidget(tab)
    assert not tab.generate_button.isEnabled()


def test_engine_unavailable_shows_notice(qtbot, tmp_path):
    """When engine unavailable, the notice label must not be hidden."""
    config = _make_config(tmp_path)
    with patch(_ENGINE_AVAILABLE, return_value=False):
        tab = SubtitleCreationTab(config)
    qtbot.addWidget(tab)
    assert not tab.engine_notice_label.isHidden()


def test_engine_available_enables_generate(qtbot, tmp_path):
    """When engine available, Generate starts enabled."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.generate_button.isEnabled()


# ---------------------------------------------------------------------------
# Output-dir permission guard
# ---------------------------------------------------------------------------


def test_unwritable_output_dir_aborts_and_no_worker(qtbot, tmp_path):
    """When output dir is not writable, Generate aborts and no worker starts."""
    config = _make_config(tmp_path)
    video = tmp_path / "test.mp4"
    video.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.file_selector.set_path(str(video))

    with (
        patch(_ENGINE_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=False),
        patch(_IS_DOWNLOADED, return_value=True),
        patch(
            _WORKER_CLS,
            side_effect=AssertionError("Worker must not be created when output not writable"),
        ),
    ):
        tab.generate_button.click()

    assert tab.worker_thread is None


def test_unwritable_output_dir_logs_error(qtbot, tmp_path):
    """When output dir is not writable, an error appears in the log widget."""
    config = _make_config(tmp_path)
    video = tmp_path / "test.mp4"
    video.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.file_selector.set_path(str(video))

    with (
        patch(_ENGINE_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=False),
        patch(_IS_DOWNLOADED, return_value=True),
    ):
        tab.generate_button.click()

    log_text = tab.log_widget.text_edit.toPlainText()
    assert "not writable" in log_text or str(video.parent) in log_text


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------


def test_generate_starts_worker_and_disables_button(qtbot, tmp_path):
    """Clicking Generate with a valid file starts the worker and disables Generate."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"fake")

    fake_worker = _FakeWorker()
    tab = _make_tab(config, qtbot)
    tab.file_selector.set_path(str(video))

    with (
        patch(_ENGINE_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_IS_DOWNLOADED, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.generate_button.click()

    assert tab.worker_thread is fake_worker
    assert fake_worker._started
    assert not tab.generate_button.isEnabled()


def test_queue_finished_re_enables_generate(qtbot, tmp_path):
    """queue_finished signal re-enables the Generate button."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"fake")

    fake_worker = _FakeWorker()
    _queue_finished_slots = []

    original_connect = fake_worker.queue_finished.connect

    def _capture_connect(slot):
        _queue_finished_slots.append(slot)
        return original_connect(slot)

    fake_worker.queue_finished.connect = _capture_connect

    tab = _make_tab(config, qtbot)
    tab.file_selector.set_path(str(video))

    with (
        patch(_ENGINE_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_IS_DOWNLOADED, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.generate_button.click()

    # Simulate queue_finished
    for slot in _queue_finished_slots:
        slot()

    assert tab.generate_button.isEnabled()


def test_file_progress_updates_progress_widget(qtbot, tmp_path):
    """file_progress(idx, pct, msg) drives the ProgressWidget."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"fake")

    fake_worker = _FakeWorker()
    _progress_slots = []

    original_connect = fake_worker.file_progress.connect

    def _capture_connect(slot):
        _progress_slots.append(slot)
        return original_connect(slot)

    fake_worker.file_progress.connect = _capture_connect

    tab = _make_tab(config, qtbot)
    tab.file_selector.set_path(str(video))

    with (
        patch(_ENGINE_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_IS_DOWNLOADED, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.generate_button.click()

    # Fire file_progress
    for slot in _progress_slots:
        slot(0, 50, "Transcribing: 50%")

    status_text = tab.progress_widget.status_label.text()
    assert "Transcribing" in status_text or "50" in status_text


def test_file_finished_success_appends_log(qtbot, tmp_path):
    """file_finished(idx, out_path, None) appends a success line to LogWidget."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"fake")
    out_srt = tmp_path / "episode.srt"

    fake_worker = _FakeWorker()
    _finished_slots = []

    original_connect = fake_worker.file_finished.connect

    def _capture_connect(slot):
        _finished_slots.append(slot)
        return original_connect(slot)

    fake_worker.file_finished.connect = _capture_connect

    tab = _make_tab(config, qtbot)
    tab.file_selector.set_path(str(video))

    with (
        patch(_ENGINE_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_IS_DOWNLOADED, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.generate_button.click()

    # Fire file_finished with success
    for slot in _finished_slots:
        slot(0, out_srt, None)

    log_text = tab.log_widget.text_edit.toPlainText()
    assert "episode.srt" in log_text or "Done" in log_text


def test_file_finished_error_appends_error_log(qtbot, tmp_path):
    """file_finished(idx, None, error_str) appends an error line to LogWidget."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"fake")

    fake_worker = _FakeWorker()
    _finished_slots = []

    original_connect = fake_worker.file_finished.connect

    def _capture_connect(slot):
        _finished_slots.append(slot)
        return original_connect(slot)

    fake_worker.file_finished.connect = _capture_connect

    tab = _make_tab(config, qtbot)
    tab.file_selector.set_path(str(video))

    with (
        patch(_ENGINE_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_IS_DOWNLOADED, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.generate_button.click()

    # Fire file_finished with error
    for slot in _finished_slots:
        slot(0, None, "Audio extraction failed")

    log_text = tab.log_widget.text_edit.toPlainText()
    assert "Audio extraction failed" in log_text


# ---------------------------------------------------------------------------
# iter_close_workers
# ---------------------------------------------------------------------------


def test_iter_close_workers_empty_when_no_worker(qtbot, tmp_path):
    """iter_close_workers() yields nothing when no worker has been started."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    workers = list(tab.iter_close_workers())
    assert workers == []


def test_iter_close_workers_returns_active_worker(qtbot, tmp_path):
    """iter_close_workers() yields the active worker when one is running."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"fake")

    fake_worker = _FakeWorker()
    tab = _make_tab(config, qtbot)
    tab.file_selector.set_path(str(video))

    with (
        patch(_ENGINE_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_IS_DOWNLOADED, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.generate_button.click()

    workers = list(tab.iter_close_workers())
    assert fake_worker in workers


# ---------------------------------------------------------------------------
# Model-not-downloaded guard
# ---------------------------------------------------------------------------


def test_model_not_downloaded_shows_dialog_on_generate(qtbot, tmp_path):
    """When model is not downloaded, Generate prompts the user (QMessageBox)."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.file_selector.set_path(str(video))

    with (
        patch(_ENGINE_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_IS_DOWNLOADED, return_value=False),
        patch("anki_miner.gui.widgets.subtitle_creation_tab.QMessageBox.warning") as mock_warn,
    ):
        tab.generate_button.click()

    mock_warn.assert_called_once()
    # Worker must NOT be started
    assert tab.worker_thread is None


# ---------------------------------------------------------------------------
# ASR smoke handler
# ---------------------------------------------------------------------------


def test_asr_smoke_handler_prints_pass_when_engine_available(capsys):
    """_run_asr_bundled_smoke() prints BUNDLED_SMOKE_PASS when engine is available
    and get_whisper_model_cls() succeeds."""
    from anki_miner.gui.app import _run_asr_bundled_smoke

    fake_cls = MagicMock(__name__="WhisperModel")

    with (
        patch("anki_miner.services.asr._engine.available", return_value=True),
        patch("anki_miner.services.asr._engine.get_whisper_model_cls", return_value=fake_cls),
    ):
        rc = _run_asr_bundled_smoke()

    captured = capsys.readouterr()
    assert rc == 0
    assert "BUNDLED_SMOKE_PASS" in captured.out


def test_asr_smoke_handler_returns_nonzero_when_engine_unavailable(capsys):
    """_run_asr_bundled_smoke() returns nonzero when engine is unavailable."""
    from anki_miner.gui.app import _run_asr_bundled_smoke

    with patch("anki_miner.services.asr._engine.available", return_value=False):
        rc = _run_asr_bundled_smoke()

    assert rc != 0
    captured = capsys.readouterr()
    assert "BUNDLED_SMOKE_FAIL" in captured.err


def test_asr_smoke_handler_returns_nonzero_on_import_error(capsys):
    """_run_asr_bundled_smoke() returns nonzero when get_whisper_model_cls raises."""
    from anki_miner.gui.app import _run_asr_bundled_smoke

    with (
        patch("anki_miner.services.asr._engine.available", return_value=True),
        patch(
            "anki_miner.services.asr._engine.get_whisper_model_cls",
            side_effect=ImportError("faster_whisper not installed"),
        ),
    ):
        rc = _run_asr_bundled_smoke()

    assert rc != 0
    captured = capsys.readouterr()
    assert "BUNDLED_SMOKE_FAIL" in captured.err

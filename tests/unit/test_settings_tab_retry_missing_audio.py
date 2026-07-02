"""Tests for the Settings -> Audio 'Retry missing expression audio' affordance."""

from __future__ import annotations

import contextlib

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets import settings_tab as settings_tab_module
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture(autouse=True)
def _no_real_styling_writes(monkeypatch):
    """Keep Save/Reset-triggered styling syncs off the real network (see
    test_settings_tab.py for the full rationale)."""
    from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController

    monkeypatch.setattr(AnkiProbeController, "_start_styling_write", lambda self, mode: None)


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    widget.shutdown()
    for w in widget.iter_close_workers():
        if w is not None:
            w.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


def _sync_run_off_thread(parent, work, on_done, on_error=None, *, error_prefix=""):
    """Drop-in for run_off_thread that runs the work inline on the calling thread."""
    try:
        result = work()
    except Exception as exc:  # noqa: BLE001 — mirror the worker error path
        if on_error is not None:
            on_error(f"{error_prefix}{exc}")
        return None
    on_done(result)
    return None


def test_panel_button_emits_retry_signal(qtbot, tmp_path):
    """Clicking the panel button emits retry_missing_audio_requested."""
    panel = AudioPackSettingsPanel(packs_root=tmp_path)
    qtbot.addWidget(panel)
    with qtbot.waitSignal(panel.retry_missing_audio_requested, timeout=1000):
        panel._retry_missing_btn.click()


def test_retry_slot_purges_markers_and_reports_count(tab, tmp_path, monkeypatch):
    """The slot unlinks jpod101/*.miss and confirms the count in a dialog."""
    jpod_cache = tmp_path / "audio_cache" / "jpod101"
    jpod_cache.mkdir(parents=True)
    (jpod_cache / "a.miss").touch()
    (jpod_cache / "b.miss").touch()
    (jpod_cache / "keep.mp3").write_bytes(b"ID3keep")

    monkeypatch.setattr(settings_tab_module, "ANKI_MINER_HOME", tmp_path)
    monkeypatch.setattr(settings_tab_module, "run_off_thread", _sync_run_off_thread)

    shown: dict[str, object] = {}
    monkeypatch.setattr(
        settings_tab_module.QMessageBox,
        "information",
        lambda parent, title, text, *a, **k: shown.update(title=title, text=text),
    )

    tab._on_retry_missing_audio()

    # Markers gone, real audio untouched.
    assert not list(jpod_cache.glob("*.miss"))
    assert (jpod_cache / "keep.mp3").exists()
    # Count surfaced and button re-enabled.
    assert "2" in str(shown["text"])
    assert tab.audio_panel._retry_missing_btn.isEnabled()


def test_retry_slot_missing_cache_dir_reports_zero(tab, tmp_path, monkeypatch):
    """No cache dir yet → 0 cleared, no crash, dialog still shown."""
    monkeypatch.setattr(settings_tab_module, "ANKI_MINER_HOME", tmp_path)
    monkeypatch.setattr(settings_tab_module, "run_off_thread", _sync_run_off_thread)

    shown: dict[str, object] = {}
    monkeypatch.setattr(
        settings_tab_module.QMessageBox,
        "information",
        lambda parent, title, text, *a, **k: shown.update(text=text),
    )

    tab._on_retry_missing_audio()

    assert "0" in str(shown["text"])
    assert tab.audio_panel._retry_missing_btn.isEnabled()


def test_retry_slot_error_reenables_button(tab, tmp_path, monkeypatch):
    """A sweep failure re-enables the button and warns instead of crashing."""
    monkeypatch.setattr(settings_tab_module, "ANKI_MINER_HOME", tmp_path)
    monkeypatch.setattr(settings_tab_module, "run_off_thread", _sync_run_off_thread)

    def _boom(_cache_dir):
        raise OSError("disk on fire")

    monkeypatch.setattr(settings_tab_module, "purge_miss_markers", _boom)

    warned: dict[str, object] = {}
    monkeypatch.setattr(
        settings_tab_module.QMessageBox,
        "warning",
        lambda parent, title, text, *a, **k: warned.update(text=text),
    )

    tab._on_retry_missing_audio()

    assert "disk on fire" in str(warned["text"])
    assert tab.audio_panel._retry_missing_btn.isEnabled()

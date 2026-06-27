"""Tests for the merged SubtitlesSettingsPanel.

Covers the alass path override/round-trip, the ASR model dropdown + download
gating, the engine-missing guidance, and the in-app alass download button.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.subtitles_settings_panel import SubtitlesSettingsPanel

_PANEL_MOD = "anki_miner.gui.widgets.panels.subtitles_settings_panel"


def _wait_state_settled(qtbot, panel) -> None:
    """Block until any in-flight off-thread state probe has been applied."""
    qtbot.waitUntil(lambda: not panel._state_in_flight, timeout=5000)


# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------


def test_panel_constructs(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    assert panel is not None


def test_panel_has_alass_selector(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.alass_selector is not None


# ---------------------------------------------------------------------------
# load_from_config
# ---------------------------------------------------------------------------


def test_load_from_config_populates_selector_from_path(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    alass_path = tmp_path / "alass"
    config = AnkiMinerConfig(alass_location=alass_path)
    panel.load_from_config(config)
    _wait_state_settled(qtbot, panel)
    assert panel.alass_selector.get_path() == str(alass_path)


def test_load_from_config_with_none_clears_selector(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(alass_location=None)
    panel.load_from_config(config)
    _wait_state_settled(qtbot, panel)
    assert panel.alass_selector.get_path() == ""


def test_load_from_config_replaces_previous_value(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    first_path = tmp_path / "alass_v1"
    second_path = tmp_path / "alass_v2"

    panel.load_from_config(AnkiMinerConfig(alass_location=first_path))
    _wait_state_settled(qtbot, panel)
    assert panel.alass_selector.get_path() == str(first_path)

    panel.load_from_config(AnkiMinerConfig(alass_location=second_path))
    _wait_state_settled(qtbot, panel)
    assert panel.alass_selector.get_path() == str(second_path)


# ---------------------------------------------------------------------------
# contribute
# ---------------------------------------------------------------------------


def test_contribute_with_path_set_returns_config_with_alass_location(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    alass_path = tmp_path / "alass"
    panel.alass_selector.set_path(str(alass_path))
    config = AnkiMinerConfig()
    new_config = panel.contribute(config)
    assert new_config.alass_location == alass_path


def test_contribute_with_empty_selector_returns_none(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.alass_selector.set_path("")
    config = AnkiMinerConfig()
    new_config = panel.contribute(config)
    assert new_config.alass_location is None


def test_contribute_does_not_mutate_original_config(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    alass_path = tmp_path / "alass"
    panel.alass_selector.set_path(str(alass_path))
    config = AnkiMinerConfig(alass_location=None)
    panel.contribute(config)
    # Original config unchanged (frozen dataclass)
    assert config.alass_location is None


def test_contribute_whitespace_only_is_treated_as_none(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.alass_selector.set_path("   ")
    config = AnkiMinerConfig()
    new_config = panel.contribute(config)
    assert new_config.alass_location is None


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_with_path(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    alass_path = tmp_path / "alass"
    config = AnkiMinerConfig(alass_location=alass_path)
    panel.load_from_config(config)
    _wait_state_settled(qtbot, panel)
    result = panel.contribute(config)
    assert result.alass_location == alass_path


def test_round_trip_with_none(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(alass_location=None)
    panel.load_from_config(config)
    _wait_state_settled(qtbot, panel)
    result = panel.contribute(config)
    assert result.alass_location is None


# ---------------------------------------------------------------------------
# ASR section — model dropdown + contribute
# ---------------------------------------------------------------------------


def test_panel_has_model_combo(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    items = [panel.model_combo.itemText(i) for i in range(panel.model_combo.count())]
    assert "large-v3" in items
    assert "small" in items


def test_contribute_preserves_asr_model_and_alass(qtbot, tmp_path):
    """contribute folds BOTH asr_model and alass_location into the new config."""
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    alass_path = tmp_path / "alass"
    config = AnkiMinerConfig(asr_model="large-v3", alass_location=None)
    panel.load_from_config(config)
    _wait_state_settled(qtbot, panel)

    panel.model_combo.setCurrentText("small")
    panel.alass_selector.set_path(str(alass_path))
    new_config = panel.contribute(config)

    assert new_config.asr_model == "small"
    assert new_config.alass_location == alass_path
    # Original frozen config untouched.
    assert config.asr_model == "large-v3"


# ---------------------------------------------------------------------------
# ASR engine-missing guidance + download gating
# ---------------------------------------------------------------------------


def test_engine_unavailable_disables_download_and_shows_guidance(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert not panel.download_model_button.isEnabled()
    assert panel._asr_engine_guidance.isVisibleTo(panel)


def test_engine_available_enables_download_and_hides_guidance(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", lambda name, root: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert panel.download_model_button.isEnabled()
    assert not panel._asr_engine_guidance.isVisibleTo(panel)
    assert "not downloaded" in panel.model_status_label.text().lower()


def test_download_click_emits_when_engine_available(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", lambda name, root: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_model="small", asr_models_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    received: list[str] = []
    panel.asr_download_requested.connect(received.append)
    panel.download_model_button.click()

    assert received == ["small"]
    assert not panel.download_model_button.isEnabled()  # disabled in flight


def test_download_click_noop_when_engine_unavailable(qtbot, tmp_path, monkeypatch):
    """A direct click handler call must not emit when the engine is missing."""
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    received: list[str] = []
    panel.asr_download_requested.connect(received.append)
    panel._on_download_clicked()

    assert received == []


def test_engine_guidance_command_copies_to_clipboard(qtbot, monkeypatch):
    from PyQt6.QtWidgets import QApplication

    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)

    panel._copy_to_clipboard('pip install "anki-miner[asr]"')
    clipboard = QApplication.clipboard()
    assert clipboard is not None
    assert clipboard.text() == 'pip install "anki-miner[asr]"'


# ---------------------------------------------------------------------------
# alass in-app download
# ---------------------------------------------------------------------------


def test_alass_download_button_emits_when_supported(qtbot, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.alass_install_supported", lambda: True)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)

    received: list[None] = []
    panel.alass_download_requested.connect(lambda: received.append(None))
    panel.download_alass_button.click()

    assert len(received) == 1
    assert not panel.download_alass_button.isEnabled()  # disabled in flight


def test_alass_status_reflects_installed_state(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.alass_install_supported", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.is_installed", lambda root: True)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(bin_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert "downloaded" in panel.alass_status_label.text().lower()
    assert panel.download_alass_button.isEnabled()


def test_unsupported_platform_has_no_alass_button(qtbot, monkeypatch):
    """On macOS (unsupported) the panel shows guidance, not a download button."""
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.alass_install_supported", lambda: False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)

    assert not hasattr(panel, "download_alass_button")
    # set_alass_status is a safe no-op when unsupported.
    panel.set_alass_status("anything")  # must not raise


# ---------------------------------------------------------------------------
# ASR device dropdown — round-trip + config marshalling
# ---------------------------------------------------------------------------


def test_panel_has_device_combo(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.device_combo is not None
    assert panel.device_combo.count() == 3


def test_device_default_is_auto(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.get_device() == "auto"


def test_set_device_round_trips(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_device("cuda")
    assert panel.get_device() == "cuda"
    panel.set_device("cpu")
    assert panel.get_device() == "cpu"
    panel.set_device("auto")
    assert panel.get_device() == "auto"


def test_set_device_unknown_falls_back_to_auto(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_device("cpu")
    panel.set_device("nonsense")
    assert panel.get_device() == "auto"


def test_load_from_config_sets_device(qtbot, tmp_path):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_device="cuda", cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)
    assert panel.get_device() == "cuda"


def test_contribute_includes_device(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_device("cpu")
    new_config = panel.contribute(AnkiMinerConfig())
    assert new_config.asr_device == "cpu"


# ---------------------------------------------------------------------------
# GPU acceleration pack download — gating + in-flight guard
# ---------------------------------------------------------------------------


def _patch_cuda(monkeypatch, *, supported: bool, gpu_count: int, installed: bool = False) -> None:
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.cuda_pack_supported", lambda: supported)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.is_installed", lambda root: installed)
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.cuda_device_count", lambda: gpu_count)


def test_cuda_button_enabled_when_supported_and_gpu_present(qtbot, tmp_path, monkeypatch):
    _patch_cuda(monkeypatch, supported=True, gpu_count=1, installed=False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert panel.download_cuda_button.isEnabled()
    assert "not installed" in panel.cuda_status_label.text().lower()


def test_cuda_status_reflects_installed(qtbot, tmp_path, monkeypatch):
    _patch_cuda(monkeypatch, supported=True, gpu_count=2, installed=True)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert panel.download_cuda_button.isEnabled()
    assert "installed" in panel.cuda_status_label.text().lower()


def test_cuda_button_disabled_when_no_gpu(qtbot, tmp_path, monkeypatch):
    _patch_cuda(monkeypatch, supported=True, gpu_count=0)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert not panel.download_cuda_button.isEnabled()


def test_cuda_button_hidden_when_unsupported(qtbot, tmp_path, monkeypatch):
    _patch_cuda(monkeypatch, supported=False, gpu_count=0)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert not panel.download_cuda_button.isEnabled()
    assert not panel.download_cuda_button.isVisibleTo(panel)


def test_cuda_download_click_emits_when_available(qtbot, tmp_path, monkeypatch):
    _patch_cuda(monkeypatch, supported=True, gpu_count=1)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    received: list[None] = []
    panel.cuda_pack_download_requested.connect(lambda: received.append(None))
    panel.download_cuda_button.click()

    assert len(received) == 1
    assert not panel.download_cuda_button.isEnabled()  # disabled in flight


def test_cuda_download_click_noop_when_unsupported(qtbot, tmp_path, monkeypatch):
    _patch_cuda(monkeypatch, supported=False, gpu_count=0)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    received: list[None] = []
    panel.cuda_pack_download_requested.connect(lambda: received.append(None))
    panel._on_cuda_pack_download_clicked()

    assert received == []


def test_cuda_in_flight_guard_lifecycle(qtbot, tmp_path, monkeypatch):
    """Click disables + flags in flight; a refresh mid-flight keeps it disabled;
    notify clears it."""
    _patch_cuda(monkeypatch, supported=True, gpu_count=1)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    panel.download_cuda_button.click()
    assert panel._cuda_pack_active
    assert not panel.download_cuda_button.isEnabled()

    # A reload (config refresh) mid-download must keep it disabled, even after
    # the off-thread probe lands.
    panel.load_from_config(AnkiMinerConfig(cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)
    assert not panel.download_cuda_button.isEnabled()

    panel.notify_cuda_pack_download_finished(tmp_path)
    _wait_state_settled(qtbot, panel)
    assert not panel._cuda_pack_active
    assert panel.download_cuda_button.isEnabled()


def test_set_cuda_pack_status_sets_label(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_cuda_pack_status("Downloading…")
    assert panel.cuda_status_label.text() == "Downloading…"


# ---------------------------------------------------------------------------
# Off-thread state probing (startup-freeze fix)
# ---------------------------------------------------------------------------


def test_heavy_probes_run_off_the_gui_thread(qtbot, tmp_path, monkeypatch):
    """The expensive ctranslate2/find_spec/disk probes must not run on the GUI
    thread when load_from_config fires (this is the startup-freeze fix)."""
    main_thread_id = threading.get_ident()
    seen: dict[str, int] = {}

    def _record(key, value):
        def _probe(*_args, **_kwargs):
            seen[key] = threading.get_ident()
            return value

        return _probe

    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", _record("available", True))
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.cuda_device_count", _record("cuda", 1))
    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", _record("model", False))
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.cuda_pack_supported", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.is_installed", _record("cuda_pack", False))
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.alass_install_supported", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.is_installed", _record("alass", False))

    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path, cuda_libs_root=tmp_path, bin_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    for key in ("available", "cuda", "model", "cuda_pack", "alass"):
        assert key in seen, f"{key} probe did not run"
        assert seen[key] != main_thread_id, f"{key} probe ran on the GUI thread"


def test_checking_status_shown_and_buttons_disabled_while_probe_in_flight(qtbot, tmp_path, monkeypatch):
    """Before the probe lands the download buttons are disabled (so a click can't
    race the probe) and a neutral status is shown."""
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.cuda_device_count", lambda: 1)
    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", lambda name, root: False)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.cuda_pack_supported", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.is_installed", lambda root: False)

    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path, cuda_libs_root=tmp_path))

    # Synchronously after load_from_config (before the worker finishes) the
    # buttons are disabled.
    assert panel._state_in_flight
    assert not panel.download_model_button.isEnabled()
    assert not panel.download_cuda_button.isEnabled()

    _wait_state_settled(qtbot, panel)
    assert panel.download_model_button.isEnabled()
    assert panel.download_cuda_button.isEnabled()


def test_cuda_device_count_cached_across_reloads(qtbot, tmp_path, monkeypatch):
    """cuda_device_count + engine.available are stable for the process lifetime;
    they must be probed once and reused on later reloads (no ctranslate2
    re-import)."""
    available_calls = {"n": 0}
    cuda_calls = {"n": 0}

    def _available():
        available_calls["n"] += 1
        return True

    def _cuda():
        cuda_calls["n"] += 1
        return 1

    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", _available)
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.cuda_device_count", _cuda)
    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", lambda name, root: False)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.cuda_pack_supported", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.is_installed", lambda root: False)

    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path, cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path, cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert cuda_calls["n"] == 1
    assert available_calls["n"] == 1


def test_install_flags_reprobed_on_each_refresh(qtbot, tmp_path, monkeypatch):
    """model_downloaded / cuda_pack_installed / alass_installed change after a
    download, so they must be re-probed on every refresh (not cached)."""
    model_calls = {"n": 0}
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.cuda_device_count", lambda: 0)

    def _model(name, root):
        model_calls["n"] += 1
        return False

    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", _model)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.cuda_pack_supported", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.is_installed", lambda root: False)

    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path, cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)
    panel.load_from_config(AnkiMinerConfig(asr_models_root=tmp_path, cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert model_calls["n"] == 2


def test_notify_asr_download_finished_reprobes_model(qtbot, tmp_path, monkeypatch):
    """A finished model download re-probes the install flag and updates the label."""
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.cuda_device_count", lambda: 0)
    state = {"downloaded": False}
    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", lambda name, root: state["downloaded"])
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.cuda_pack_supported", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.is_installed", lambda root: False)

    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(asr_model="small", asr_models_root=tmp_path, cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)
    assert "not downloaded" in panel.model_status_label.text().lower()

    state["downloaded"] = True
    panel.notify_asr_download_finished("small", tmp_path)
    _wait_state_settled(qtbot, panel)

    assert not panel._asr_download_active
    assert panel.model_status_label.text().lower() == "downloaded"


def test_notify_alass_download_finished_reprobes_install(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.cuda_device_count", lambda: 0)
    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", lambda name, root: False)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.cuda_pack_supported", lambda: False)
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.alass_install_supported", lambda: True)
    state = {"installed": False}
    monkeypatch.setattr(f"{_PANEL_MOD}.alass_installer.is_installed", lambda root: state["installed"])

    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(bin_root=tmp_path))
    _wait_state_settled(qtbot, panel)
    assert "not downloaded" in panel.alass_status_label.text().lower()

    state["installed"] = True
    panel.notify_alass_download_finished()
    _wait_state_settled(qtbot, panel)

    assert not panel._alass_download_active
    assert panel.alass_status_label.text().lower() == "downloaded"


def test_inflight_reload_redispatches_latest(qtbot, tmp_path, monkeypatch):
    """A reload requested while a probe is in flight is not dropped: the latest
    config wins via a single trailing re-dispatch."""
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.available", lambda: True)
    monkeypatch.setattr(f"{_PANEL_MOD}._engine.cuda_device_count", lambda: 0)
    seen_models: list = []

    def _is_downloaded(name, root):
        seen_models.append(root)
        return False

    monkeypatch.setattr(f"{_PANEL_MOD}.model_manager.is_downloaded", _is_downloaded)
    monkeypatch.setattr(f"{_PANEL_MOD}.cuda_pack_installer.cuda_pack_supported", lambda: False)

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()

    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    # First load starts a probe; second load while in flight must re-dispatch
    # with root_b after the first lands.
    panel.load_from_config(AnkiMinerConfig(asr_model="small", asr_models_root=root_a, cuda_libs_root=tmp_path))
    panel.load_from_config(AnkiMinerConfig(asr_model="small", asr_models_root=root_b, cuda_libs_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    # The trailing (latest) config's models_root must have been probed.
    assert root_b in seen_models


# ---------------------------------------------------------------------------
# Silence-removal (VAD) pack download section
# ---------------------------------------------------------------------------


def _patch_vad(monkeypatch, *, ort_present: bool, supported: bool, installed: bool = False) -> None:
    """Control onnxruntime importability + pack support/install for the panel."""
    import importlib.util as iu

    real_find_spec = iu.find_spec

    def fake_find_spec(name, *a, **kw):
        if name == "onnxruntime":
            return object() if ort_present else None
        return real_find_spec(name, *a, **kw)

    monkeypatch.setattr(iu, "find_spec", fake_find_spec)
    monkeypatch.setattr(f"{_PANEL_MOD}.onnx_pack_installer.onnx_pack_supported", lambda: supported)
    monkeypatch.setattr(f"{_PANEL_MOD}.onnx_pack_installer.is_installed", lambda root: installed)


def test_vad_button_hidden_when_onnxruntime_present(qtbot, tmp_path, monkeypatch):
    _patch_vad(monkeypatch, ort_present=True, supported=True)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(onnx_pack_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert not panel.download_vad_button.isVisibleTo(panel)
    assert panel._vad_guidance_label.isVisibleTo(panel)


def test_vad_button_enabled_when_supported_not_installed(qtbot, tmp_path, monkeypatch):
    _patch_vad(monkeypatch, ort_present=False, supported=True, installed=False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(onnx_pack_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert panel.download_vad_button.isEnabled()
    assert "not installed" in panel.vad_status_label.text().lower()


def test_vad_status_installed_when_present_on_disk(qtbot, tmp_path, monkeypatch):
    _patch_vad(monkeypatch, ort_present=False, supported=True, installed=True)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(onnx_pack_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert panel.download_vad_button.isEnabled()
    assert "installed" in panel.vad_status_label.text().lower()


def test_vad_button_hidden_when_unsupported(qtbot, tmp_path, monkeypatch):
    _patch_vad(monkeypatch, ort_present=False, supported=False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(onnx_pack_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    assert not panel.download_vad_button.isEnabled()
    assert not panel.download_vad_button.isVisibleTo(panel)
    assert panel._vad_guidance_label.isVisibleTo(panel)


def test_vad_download_click_emits_when_supported(qtbot, tmp_path, monkeypatch):
    _patch_vad(monkeypatch, ort_present=False, supported=True)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(onnx_pack_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    received: list[None] = []
    panel.vad_pack_download_requested.connect(lambda: received.append(None))
    panel.download_vad_button.click()

    assert len(received) == 1
    assert not panel.download_vad_button.isEnabled()  # disabled in flight


def test_vad_download_click_noop_when_unsupported(qtbot, tmp_path, monkeypatch):
    _patch_vad(monkeypatch, ort_present=False, supported=False)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(onnx_pack_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    received: list[None] = []
    panel.vad_pack_download_requested.connect(lambda: received.append(None))
    panel._on_vad_pack_download_clicked()
    _wait_state_settled(qtbot, panel)

    assert received == []


def test_vad_in_flight_guard_lifecycle(qtbot, tmp_path, monkeypatch):
    """Click disables + flags in flight; a refresh mid-flight keeps it disabled;
    notify clears it."""
    _patch_vad(monkeypatch, ort_present=False, supported=True)
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig(onnx_pack_root=tmp_path))
    _wait_state_settled(qtbot, panel)

    panel.download_vad_button.click()
    assert panel._vad_pack_active
    assert not panel.download_vad_button.isEnabled()

    # A reload (config refresh) mid-download must keep it disabled.
    panel.load_from_config(AnkiMinerConfig(onnx_pack_root=tmp_path))
    assert not panel.download_vad_button.isEnabled()

    panel.notify_vad_pack_download_finished(tmp_path)
    _wait_state_settled(qtbot, panel)
    assert not panel._vad_pack_active
    assert panel.download_vad_button.isEnabled()


def test_set_vad_pack_status_sets_label(qtbot):
    panel = SubtitlesSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_vad_pack_status("Downloading…")
    assert panel.vad_status_label.text() == "Downloading…"

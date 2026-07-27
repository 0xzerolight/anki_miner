"""Installed-artifact GUI smoke coverage (W12)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[2]


INSTALLER_SMOKE_PROBE = r"""
from anki_miner.gui import app, main_window
from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase
from anki_miner.gui.widgets.panels import subtitles_settings_panel
from anki_miner.gui.workers.prewarm_worker import PrewarmWorker
from anki_miner.services.asr import _engine
from anki_miner.services import alass_installer

def forbidden(*args, **kwargs):
    raise AssertionError("suppressed installer-smoke startup work ran")

_real_boot_step = main_window.MainWindow._run_optional_boot_step

def only_profiles(name, step):
    # The settings-profile reconcile is the ONE boot step the suppressed path
    # runs: it is pure local file I/O and it seeds the active-profile marker
    # that the config save below — the file this smoke asserts on — stamps.
    if name != "settings profiles":
        raise AssertionError("suppressed installer-smoke startup work ran: " + name)
    _real_boot_step(name, step)

main_window.MainWindow._run_optional_boot_step = staticmethod(only_profiles)
main_window.MainWindow._maybe_create_shortcut_on_first_run = forbidden
main_window.MainWindow._maybe_offer_first_run_setup = forbidden
main_window.MainWindow._maybe_prompt_stale_dictionaries = forbidden
app.install_stall_watchdog = forbidden
app._start_stats_load = forbidden
PrewarmWorker.start = forbidden
_ToolTabBase._run_availability_scan = forbidden
subtitles_settings_panel.SubtitlesSettingsPanel._refresh_state_async = forbidden
alass_installer.alass_install_supported = forbidden
_engine.whisper_cpp_available = forbidden

app.main()
"""


INSTALLER_SMOKE_FAILURE_PROBE = r"""
import os
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox

from anki_miner.gui import app

dialog_marker = Path(os.environ["ANKI_MINER_DIALOG_MARKER"])

def record_dialog(*args, **kwargs):
    dialog_marker.write_text("dialog shown", encoding="utf-8")
    raise AssertionError("installer smoke must never show a modal dialog")

QMessageBox.exec = record_dialog
QMessageBox.critical = record_dialog
QMessageBox.information = record_dialog
QMessageBox.warning = record_dialog
QMessageBox.question = record_dialog
app.main()
"""


def _smoke_env(home: Path, result_path: Path, *, expected_version: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ANKI_MINER_HOME": str(home),
            "ANKI_MINER_SMOKE": "installer",
            "ANKI_MINER_SMOKE_RESULT": str(result_path),
            "ANKI_MINER_SMOKE_EXPECTED_VERSION": expected_version,
            "PYTHONPATH": os.pathsep.join((str(PROJECT_ROOT), env.get("PYTHONPATH", ""))),
            "QT_QPA_PLATFORM": "offscreen",
        }
    )
    return env


def _run_smoke_probe(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )


def test_commit_boot_suppression_keeps_only_required_version_commit(
    qtbot,
    monkeypatch,
    patch_heavy_init,
    test_config,
) -> None:
    from anki_miner.gui import main_window as main_window_module

    config = replace(
        test_config,
        last_known_version="0.0.0",
        check_for_updates=True,
        first_run_shortcut_done=False,
        first_run_setup_done=False,
    )
    patch_heavy_init(config)
    saved = []
    monkeypatch.setattr(main_window_module.GUIConfigManager, "save_config", saved.append)
    steps: list[str] = []
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_run_optional_boot_step",
        staticmethod(lambda name, step: steps.append(name)),
    )
    monkeypatch.setattr(
        main_window_module.QTimer,
        "singleShot",
        lambda *args, **kwargs: pytest.fail("deferred first-run work was scheduled"),
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "information",
        lambda *args, **kwargs: pytest.fail("version dialog shown"),
    )
    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)

    window.commit_boot(suppress_optional=True)

    assert window._boot_committed is True
    assert window.config.last_known_version == __version__
    assert [item.last_known_version for item in saved] == [__version__]
    # Exactly one step survives suppression: the settings-profile reconcile,
    # which seeds the active-profile marker the save above has to carry.
    assert steps == ["settings profiles"]


def test_composition_suppression_builds_every_tab_without_availability_probes(
    qtbot,
    monkeypatch,
    patch_heavy_init,
    test_config,
) -> None:
    from anki_miner.gui import app as app_module
    from anki_miner.gui.widgets._tool_tab_base import _ToolTabBase
    from anki_miner.gui.widgets.panels import subtitles_settings_panel
    from anki_miner.services import alass_installer
    from anki_miner.services.asr import _engine

    patch_heavy_init(test_config)

    def forbidden(*args, **kwargs):
        pytest.fail("constructor-time availability probe ran")

    monkeypatch.setattr(_ToolTabBase, "_run_availability_scan", forbidden)
    monkeypatch.setattr(subtitles_settings_panel.SubtitlesSettingsPanel, "_refresh_state_async", forbidden)
    monkeypatch.setattr(alass_installer, "alass_install_supported", forbidden)
    monkeypatch.setattr(_engine, "whisper_cpp_available", forbidden)

    composed = app_module.compose_main_window(test_config, suppress_optional_startup=True)
    qtbot.addWidget(composed.window)

    assert [composed.window.tabs.tabText(index) for index in range(composed.window.tabs.count())] == [
        "Video",
        "Deck Builder",
        "Audiobooks",
        "Reading",
        "Analytics",
        "Utilities",
        "Settings",
    ]


def test_installer_smoke_runs_full_gui_and_writes_ready_result(tmp_path: Path) -> None:
    home = tmp_path / "am home 日本語"
    result_path = tmp_path / "installer-ready.txt"

    result = _run_smoke_probe(
        INSTALLER_SMOKE_PROBE,
        _smoke_env(home, result_path, expected_version=__version__),
    )

    assert result.returncode == 0, result.stderr
    assert result_path.read_bytes() == f"ANKI_MINER_INSTALLER_READY {__version__}\n".encode()
    assert (home / "gui_config.json").is_file()
    assert (home / "anki_miner.log").is_file()
    assert (home / "dicts").is_dir()
    # End-to-end proof of the boot-step placement, in a real process with the
    # real save_config: the reconcile created the default profile AND ran early
    # enough that the marker reached gui_config.json.
    assert (home / "profiles" / "default.json").is_file()
    saved_config = json.loads((home / "gui_config.json").read_text(encoding="utf-8"))
    assert saved_config["active_profile_id"] == "default"


def test_installer_smoke_failure_exits_nonzero_without_dialog(tmp_path: Path) -> None:
    home = tmp_path / "failure home 日本語"
    result_path = tmp_path / "must-not-exist.txt"
    dialog_marker = tmp_path / "dialog-shown.txt"
    env = _smoke_env(home, result_path, expected_version="wrong-version")
    env["ANKI_MINER_DIALOG_MARKER"] = str(dialog_marker)

    result = _run_smoke_probe(INSTALLER_SMOKE_FAILURE_PROBE, env)

    assert result.returncode == 1
    assert not result_path.exists()
    assert not dialog_marker.exists()
    assert "CRITICAL" in (home / "anki_miner.log").read_text(encoding="utf-8")

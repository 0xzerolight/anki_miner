"""Boot-time legacy pitch migration: LIVE config must update, not just disk.

Pins the update_config route: a bare GUIConfigManager.save_config would leave
the running session's config with an empty pitch_chain (pitch inactive until
restart) while the on-disk JSON looks migrated — so these tests assert the
window's live config and the config_version bump, not the saved file.
"""

from __future__ import annotations

from dataclasses import replace

from anki_miner.config import PitchSourceEntry


def _patch_window_construction(monkeypatch) -> None:
    from anki_miner.gui import main_window as main_window_module

    monkeypatch.setattr(main_window_module.ValidationService, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_maybe_repair_legacy_frequency_source_name",
        lambda self: None,
    )
    monkeypatch.setattr(main_window_module.QTimer, "singleShot", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_window_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(main_window_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(main_window_module.MainWindow, "_maybe_migrate_jmdict", lambda self: None)
    monkeypatch.setattr(main_window_module.MainWindow, "_maybe_start_ytdlp_update", lambda self: None)


def _make_window(qtbot, monkeypatch, config):
    from anki_miner.gui import main_window as main_window_module

    saved = []
    monkeypatch.setattr(main_window_module.GUIConfigManager, "save_config", lambda cfg: saved.append(cfg))
    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)
    return window, saved


def test_boot_migrates_legacy_csv_into_live_config(qtbot, monkeypatch, tmp_path, test_config):
    _patch_window_construction(monkeypatch)
    legacy_csv = tmp_path / "pitch_accent.csv"
    legacy_csv.write_text("ねこ,猫,1\n", encoding="utf-8")
    config = replace(
        test_config,
        pitch_accent_path=legacy_csv,
        pitch_root=tmp_path / "pitch",
        pitch_chain=(),
        first_run_shortcut_done=True,
        first_run_setup_done=True,
    )
    window, saved = _make_window(qtbot, monkeypatch, config)
    version_before = window.config.config_version

    window.commit_boot()

    # LIVE config carries the chain (not merely the on-disk JSON)...
    assert window.config.pitch_chain == (PitchSourceEntry("legacy-pitch"),)
    assert window.config.pitch_active is True
    # ...through update_config: version bumped and the migrated config saved.
    assert window.config.config_version > version_before
    assert any(cfg.pitch_chain for cfg in saved)
    assert (tmp_path / "pitch" / "legacy-pitch" / "index.sqlite").is_file()
    window.deleteLater()


def test_boot_noops_without_legacy_csv(qtbot, monkeypatch, tmp_path, test_config):
    _patch_window_construction(monkeypatch)
    config = replace(
        test_config,
        pitch_accent_path=tmp_path / "absent.csv",
        pitch_root=tmp_path / "pitch",
        pitch_chain=(),
        first_run_shortcut_done=True,
        first_run_setup_done=True,
    )
    window, _saved = _make_window(qtbot, monkeypatch, config)

    window.commit_boot()

    assert window.config.pitch_chain == ()
    assert window.config.pitch_active is False
    window.deleteLater()


def test_boot_migration_failure_never_crashes_startup(qtbot, monkeypatch, tmp_path, test_config):
    from anki_miner.gui import main_window as main_window_module

    _patch_window_construction(monkeypatch)
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_maybe_migrate_legacy_pitch",
        lambda self: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    config = replace(
        test_config,
        first_run_shortcut_done=True,
        first_run_setup_done=True,
    )
    window, _saved = _make_window(qtbot, monkeypatch, config)

    window.commit_boot()  # optional boot step guard swallows the failure

    assert window.config.pitch_chain == ()
    window.deleteLater()

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config import AudioSourceEntry
from anki_miner.gui.main_window import MainWindow
from anki_miner.gui.utils.config_commit import ConfigCommitError, ConfigCommitResult
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.settings_tab import SettingsTab


def test_update_config_marks_save_failure_as_pre_save(
    qtbot,
    patch_heavy_init,
    test_config,
    monkeypatch,
) -> None:
    patch_heavy_init(test_config)
    window = MainWindow()
    qtbot.addWidget(window)
    save_calls = 0

    def fail_once(_config) -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise OSError("disk full")

    monkeypatch.setattr(GUIConfigManager, "save_config", fail_once)

    with pytest.raises(ConfigCommitError) as raised:
        window.update_config(replace(test_config, anki_deck_name="changed"))

    assert raised.value.result.persisted is False
    assert raised.value.result.refreshed is False
    assert isinstance(raised.value.result.error, OSError)
    assert window.config.anki_deck_name == test_config.anki_deck_name


def test_update_config_marks_refresh_failure_as_post_save(
    qtbot,
    patch_heavy_init,
    test_config,
    monkeypatch,
) -> None:
    patch_heavy_init(test_config)
    window = MainWindow()
    qtbot.addWidget(window)
    saved = []
    refreshed = []
    window.config_refreshed.connect(refreshed.append)
    monkeypatch.setattr(GUIConfigManager, "save_config", saved.append)
    monkeypatch.setattr(
        window,
        "_build_config_bound_services",
        lambda: (_ for _ in ()).throw(RuntimeError("refresh failed")),
    )
    changed = replace(test_config, anki_deck_name="changed")

    with pytest.raises(ConfigCommitError) as raised:
        window.update_config(changed)

    assert raised.value.result.persisted is True
    assert raised.value.result.refreshed is False
    assert isinstance(raised.value.result.error, RuntimeError)
    assert saved
    assert refreshed == [window.config]
    assert window.config.anki_deck_name == "changed"


@pytest.mark.parametrize("persisted", [False, True])
def test_settings_tab_preserves_remove_commit_phase(
    qtbot,
    test_config,
    persisted: bool,
) -> None:
    expected = (
        ConfigCommitResult.post_save_failure(RuntimeError("refresh failed"))
        if persisted
        else ConfigCommitResult.pre_save_failure(OSError("disk full"))
    )

    def fail_commit(_config) -> None:
        raise ConfigCommitError(expected)

    tab = SettingsTab(
        test_config,
        commit_config=fail_commit,
        suppress_optional_startup=True,
    )
    qtbot.addWidget(tab)
    try:
        actual = tab._commit_dictionary_removal(())
    finally:
        tab.shutdown()
        for worker in tab.iter_close_workers():
            if worker is not None:
                worker.wait(3000)

    assert actual is expected


@pytest.mark.parametrize("persisted", [False, True])
def test_settings_tab_audio_remove_adopts_only_persisted_chain(
    qtbot,
    test_config,
    persisted: bool,
) -> None:
    entry = AudioSourceEntry(kind="custom", url="http://h/?t={term}")
    config = replace(test_config, expression_audio_chain=(entry,))
    expected = (
        ConfigCommitResult.post_save_failure(RuntimeError("refresh failed"))
        if persisted
        else ConfigCommitResult.pre_save_failure(OSError("disk full"))
    )

    def fail_commit(_config) -> None:
        raise ConfigCommitError(expected)

    tab = SettingsTab(
        config,
        commit_config=fail_commit,
        suppress_optional_startup=True,
    )
    qtbot.addWidget(tab)
    try:
        actual = tab._commit_audio_removal(())
    finally:
        tab.shutdown()
        for worker in tab.iter_close_workers():
            if worker is not None:
                worker.wait(3000)

    assert actual is expected
    assert tab.config.expression_audio_chain == (() if persisted else (entry,))

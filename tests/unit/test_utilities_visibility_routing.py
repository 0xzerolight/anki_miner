"""Hidden Utilities tools, end to end through the real window wiring.

Built by ``wired_window`` (``app.compose_main_window``) with the shared
config's ``hidden_utilities`` overridden, so the real SubtitlesTab, SettingsTab
and MainWindow routing are what answer.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskSpec
from anki_miner.gui.utils import session_state


@pytest.fixture
def test_config(test_config):
    """The shared config with Generate and Retime taken off the Utilities tab."""
    return replace(test_config, hidden_utilities=("generate", "retime"))


def _utilities(window):
    return window.tabs.widget(window._main_tab_index("subtitles"))


def _settings(window):
    return window.tabs.widget(window._settings_tab_index())


def test_a_saved_route_to_a_hidden_tool_opens_a_visible_one(wired_window):
    """Review Focus 2: the last session ended on Retime; Retime is hidden now."""
    window, _titles, _tabs = wired_window
    session_state.save_route("subtitles", {"subtitles": "retime"})

    window.restore_session_state()

    utilities = _utilities(window)
    assert window._current_main_tab_key() == "subtitles"
    assert utilities.current_subtab_key() == "condense"
    assert utilities._inner_tabs.isTabVisible(utilities._inner_tabs.currentIndex())


def test_the_guide_open_on_a_hidden_tool_focuses_its_checkbox(wired_window, qtbot):
    """Review Focus 3: run_capability_browser hands its choice to reveal_capability."""
    window, _titles, _tabs = wired_window
    settings = _settings(window)
    box = settings.ui_panel.utility_checkboxes["retime"]

    window.reveal_capability(CapabilityTarget("subtitles", "retime"))

    qtbot.waitUntil(lambda: settings.focusWidget() is box, timeout=2000)
    assert window._current_main_tab_key() == "settings"
    assert settings.current_subtab_key() == "ui"
    assert not box.isChecked()


def test_a_hidden_tools_task_opens_its_checkbox(wired_window, qtbot):
    """Review Focus 4: the run keeps going; choosing it leads to the checkbox."""
    window, _titles, _tabs = wired_window
    settings = _settings(window)
    box = settings.ui_panel.utility_checkboxes["retime"]
    window.task_registry.start(
        TaskSpec("tools.retime", "Retiming ep01", CapabilityTarget("subtitles", "retime")),
        now=0.0,
    )
    try:
        window.status_bar.task_activated.emit("tools.retime")

        qtbot.waitUntil(lambda: settings.focusWidget() is box, timeout=2000)
        assert window._current_main_tab_key() == "settings"
    finally:
        window.task_registry.shutdown()


def test_unchecking_the_open_tool_hides_it_live(wired_window, qtbot):
    """The Settings box reaches the tab through window.update_config → config_refreshed."""
    window, _titles, _tabs = wired_window
    utilities = _utilities(window)
    utilities.open_subtab("condense")

    _settings(window).ui_panel.utility_checkboxes["condense"].setChecked(False)
    for child in (
        utilities.generate_tab,
        utilities.retime_tab,
        utilities.condense_tab,
        utilities.download_tab,
        utilities.mokuro_tab,
        utilities.booksync_tab,
    ):
        assert child._availability_worker.wait(3000)
    qtbot.wait(10)

    assert window.config.hidden_utilities == ("generate", "retime", "condense")
    assert not utilities._inner_tabs.isTabVisible(utilities._subtab_index["condense"])
    assert utilities.current_subtab_key() == "backfill"

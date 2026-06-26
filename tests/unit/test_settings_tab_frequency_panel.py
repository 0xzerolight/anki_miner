"""SettingsTab integration for the multi-source Frequency panel.

Replaces the old single-file frequency selector tests: opening loads the chain
+ global toggle into the panel; saving writes ``frequency_chain`` +
``use_frequency_data`` into the config; the OLD ``frequency_selector`` /
``_resolve_frequency_path`` no longer exist (pitch's single-file flow stays).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget


# ---------------------------------------------------------------------------
# Removal of the old single-file frequency UI
# ---------------------------------------------------------------------------


def test_old_resolve_frequency_path_removed(tab):
    assert not hasattr(tab, "_resolve_frequency_path")


def test_old_frequency_selector_removed(tab):
    assert not hasattr(tab.dictionary_panel, "frequency_selector")
    assert not hasattr(tab.dictionary_panel, "use_frequency_checkbox")


def test_pitch_resolver_and_selector_still_present(tab):
    # Pitch keeps the single-file zip-import flow.
    assert hasattr(tab, "_resolve_pitch_accent_path")
    assert hasattr(tab.dictionary_panel, "pitch_accent_selector")
    assert hasattr(tab.dictionary_panel, "use_pitch_accent_checkbox")


def test_frequency_panel_present(tab):
    assert hasattr(tab, "frequency_panel")
    assert hasattr(tab, "_frequency_import_flow")


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def test_load_reflects_frequency_chain_and_toggle(test_config: AnkiMinerConfig, qtbot):
    cfg = replace(
        test_config,
        frequency_chain=(FreqEntry(source_id="jpdb", enabled=True),),
        use_frequency_data=True,
    )
    tab = SettingsTab(cfg)
    qtbot.addWidget(tab)

    assert tab.frequency_panel.get_chain() == (FreqEntry(source_id="jpdb", enabled=True),)
    assert tab.frequency_panel.use_frequency_checkbox.isChecked() is True


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def test_save_writes_frequency_chain_and_toggle(tab, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: None)

    tab.frequency_panel.set_chain(
        (FreqEntry(source_id="a", enabled=True), FreqEntry(source_id="b", enabled=False)),
        registry_meta={},
    )
    tab.frequency_panel.use_frequency_checkbox.setChecked(True)

    received: list[AnkiMinerConfig] = []
    tab.config_changed.connect(received.append)

    tab._on_save_clicked()

    assert received
    cfg = received[-1]
    assert cfg.frequency_chain == (
        FreqEntry(source_id="a", enabled=True),
        FreqEntry(source_id="b", enabled=False),
    )
    assert cfg.use_frequency_data is True


def test_chain_change_persists_immediately(tab):
    received: list[AnkiMinerConfig] = []
    tab.config_changed.connect(received.append)

    # Emitting chain_changed (e.g. reorder/toggle) persists via the narrow path.
    tab.frequency_panel.set_chain(
        (FreqEntry(source_id="a", enabled=True),),
        registry_meta={},
    )
    tab.frequency_panel.chain_changed.emit()

    assert received
    assert received[-1].frequency_chain == (FreqEntry(source_id="a", enabled=True),)

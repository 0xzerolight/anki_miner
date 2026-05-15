"""Smoke tests for DictionarySettingsPanel."""

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_panel_renders_default_chain(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.DICTS_ROOT",
        tmp_path,
    )
    panel = DictionarySettingsPanel()
    panel.set_chain(AnkiMinerConfig().dictionary_chain)
    chain = panel.get_chain()
    # Default has two entries; one indexed (missing on disk -> keeps entry), one jisho
    assert len(chain) == 2
    assert chain[1].kind == "jisho"


def test_reorder_moves_entry_up(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.DICTS_ROOT",
        tmp_path,
    )
    panel = DictionarySettingsPanel()
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="indexed", dict_id="b", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    panel.move_up(1)  # move b up
    chain = panel.get_chain()
    assert chain[0].dict_id == "b"
    assert chain[1].dict_id == "a"

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


def test_chain_changed_emits_on_reorder_remove_and_toggle(qapp, monkeypatch, tmp_path):
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

    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.move_up(1)
    assert events == ["changed"]

    panel.move_down(0)
    assert events == ["changed", "changed"]

    panel.remove(0)
    assert events == ["changed", "changed", "changed"]

    # Toggle checkbox via row widget -> chain_changed should fire
    row = panel._row_widget(0)
    assert row is not None
    row.checkbox.setChecked(not row.checkbox.isChecked())
    assert events[-1] == "changed"
    assert len(events) == 4


def test_jisho_remove_is_noop(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.DICTS_ROOT",
        tmp_path,
    )
    panel = DictionarySettingsPanel()
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.remove(1)  # jisho row -> no-op, no signal
    chain = panel.get_chain()
    assert len(chain) == 2
    assert chain[1].kind == "jisho"
    assert events == []


def test_edge_reorder_calls_are_noops(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.DICTS_ROOT",
        tmp_path,
    )
    panel = DictionarySettingsPanel()
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.move_up(0)  # top row
    panel.move_down(1)  # bottom row
    panel.move_up(-1)  # no selection
    panel.move_down(-1)
    panel.remove(-1)

    assert events == []
    chain = panel.get_chain()
    assert chain[0].dict_id == "a"
    assert chain[1].kind == "jisho"


def test_checkbox_toggle_preserved_on_reorder(qapp, monkeypatch, tmp_path):
    """The implementer's deviation: get_chain()-resync before mutation must
    preserve a user's checkbox toggle across move_up/move_down/remove."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.DICTS_ROOT",
        tmp_path,
    )
    panel = DictionarySettingsPanel()
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="indexed", dict_id="b", enabled=True),
            ChainEntry(kind="indexed", dict_id="c", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    # User unchecks row "b" (index 1) — _chain still says enabled=True until
    # get_chain() / mutation re-syncs.
    row_b = panel._row_widget(1)
    assert row_b is not None
    row_b.checkbox.setChecked(False)

    # Move "b" up. The disabled state should travel with it.
    panel.move_up(1)
    chain = panel.get_chain()
    assert chain[0].dict_id == "b"
    assert chain[0].enabled is False
    assert chain[1].dict_id == "a"
    assert chain[1].enabled is True

    # Now move "b" back down via move_down — toggle should still survive.
    panel.move_down(0)
    chain = panel.get_chain()
    assert chain[0].dict_id == "a"
    assert chain[0].enabled is True
    assert chain[1].dict_id == "b"
    assert chain[1].enabled is False

    # Uncheck "c" (now at index 2) then remove "a" (index 0); "c" toggle must
    # survive the remove's _chain rebuild.
    row_c = panel._row_widget(2)
    assert row_c is not None
    row_c.checkbox.setChecked(False)
    panel.remove(0)
    chain = panel.get_chain()
    assert [e.dict_id for e in chain[:2]] == ["b", "c"]
    assert chain[0].enabled is False
    assert chain[1].enabled is False

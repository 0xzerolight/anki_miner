"""Tests for OVH-032: dictionary chain reorder/toggle instant persist.

chain_changed must be wired to _persist_chain_change (mirroring the audio
panel).  A destructive remove re-emits chain_changed (and nothing else), so the
single wiring persists a removal exactly once.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


@pytest.fixture
def confirm_remove(monkeypatch):
    """Auto-accept the 'Remove dictionary' QMessageBox confirmation."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )


def _set_two_entry_chain(tab: SettingsTab) -> None:
    """Load a deterministic 2-indexed + jisho chain into the dict panel."""
    tab.dictionary_panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="alpha", enabled=True),
            ChainEntry(kind="indexed", dict_id="beta", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )


class TestDictChainReorderPersists:
    """Reorder and toggle must persist via chain_changed."""

    def test_move_up_persists_new_chain(self, tab):
        """Moving a dict up emits chain_changed → _persist_chain_change is called."""
        _set_two_entry_chain(tab)
        persisted: list[tuple] = []
        tab.config_changed.connect(lambda cfg: persisted.append(cfg.dictionary_chain))

        tab.dictionary_panel.move_up(1)  # move beta up

        assert len(persisted) == 1, "chain_changed must trigger exactly one persist"
        chain = persisted[0]
        assert chain[0].dict_id == "beta"
        assert chain[1].dict_id == "alpha"

    def test_move_down_persists_new_chain(self, tab):
        """Moving a dict down emits chain_changed → _persist_chain_change is called."""
        _set_two_entry_chain(tab)
        persisted: list[tuple] = []
        tab.config_changed.connect(lambda cfg: persisted.append(cfg.dictionary_chain))

        tab.dictionary_panel.move_down(0)  # move alpha down

        assert len(persisted) == 1
        chain = persisted[0]
        assert chain[0].dict_id == "beta"
        assert chain[1].dict_id == "alpha"

    def test_toggle_persists_new_chain(self, tab):
        """Toggling a row checkbox emits chain_changed → persist fires."""
        _set_two_entry_chain(tab)
        persisted: list[tuple] = []
        tab.config_changed.connect(lambda cfg: persisted.append(cfg.dictionary_chain))

        row = tab.dictionary_panel._row_widget(0)
        assert row is not None
        row.checkbox.setChecked(False)

        assert len(persisted) == 1
        chain = persisted[0]
        assert chain[0].enabled is False

    def test_toggle_updates_self_config(self, tab):
        """After toggle, tab.config.dictionary_chain reflects the new state."""
        _set_two_entry_chain(tab)

        row = tab.dictionary_panel._row_widget(0)
        assert row is not None
        row.checkbox.setChecked(False)

        assert tab.config.dictionary_chain[0].enabled is False


class TestDictChainRemovalPersistsExactlyOnce:
    """Removal must persist exactly once (a single chain_changed emit)."""

    def test_remove_persists_exactly_once(self, tab, confirm_remove, tmp_path, qtbot):
        """Removing a dict triggers chain_changed → exactly one config_changed emit."""
        # Create a physical dir so rmtree doesn't fail.
        dict_dir = tmp_path / "alpha"
        dict_dir.mkdir()
        (dict_dir / "index.sqlite").write_bytes(b"placeholder")

        # Point the panel at tmp_path so the remove finds the dir.
        tab.dictionary_panel.set_dicts_root(tmp_path)
        tab.dictionary_panel.set_chain(
            (
                ChainEntry(kind="indexed", dict_id="alpha", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            )
        )

        persisted: list[tuple] = []
        tab.config_changed.connect(lambda cfg: persisted.append(cfg.dictionary_chain))

        tab.dictionary_panel.remove(0)

        # remove()'s rmtree now runs off the GUI thread; the persist fires once
        # the chain_changed signal lands after the delete completes.
        qtbot.waitUntil(lambda: len(persisted) >= 1, timeout=3000)
        assert len(persisted) == 1, f"removal must persist exactly once; got {len(persisted)} emits"
        chain = persisted[0]
        assert len(chain) == 1
        assert chain[0].kind == "jisho"

    def test_remove_updates_self_config(self, tab, confirm_remove, tmp_path, qtbot):
        """After removal, tab.config.dictionary_chain no longer includes removed entry."""
        dict_dir = tmp_path / "alpha"
        dict_dir.mkdir()
        (dict_dir / "index.sqlite").write_bytes(b"placeholder")

        tab.dictionary_panel.set_dicts_root(tmp_path)
        tab.dictionary_panel.set_chain(
            (
                ChainEntry(kind="indexed", dict_id="alpha", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            )
        )

        tab.dictionary_panel.remove(0)

        # remove()'s rmtree now runs off the GUI thread; wait for the config to
        # be updated via the post-delete chain_changed persist.
        qtbot.waitUntil(lambda: len(tab.config.dictionary_chain) == 1, timeout=3000)
        assert tab.config.dictionary_chain[0].kind == "jisho"

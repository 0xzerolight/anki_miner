"""Tests for DictionaryImportFlow.restore_unlisted — recover orphaned on-disk dicts.

Covers: nothing-to-restore early exit, user confirms and orphan is inserted
before jisho, user declines (no chain mutation), and schema-mismatched dicts
are excluded from the offer.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.services.dictionary.storage import SCHEMA_VERSION, create_index, write_meta


def _make_dict_on_disk(
    dicts_root: Path,
    dict_id: str,
    *,
    fmt: str,
    source_name: str,
    with_source_zip: bool = False,
    schema_version: int | None = None,
) -> Path:
    """Create a dict folder with index.sqlite and optional source.zip.

    ``schema_version`` defaults to the current SCHEMA_VERSION. Pass a different
    value to simulate a schema-mismatched dict (schema_ok=False in DictMeta).
    """
    dict_dir = dicts_root / dict_id
    dict_dir.mkdir(parents=True, exist_ok=True)
    db_path = dict_dir / "index.sqlite"
    create_index(db_path)
    write_meta(
        db_path,
        {
            "schema_version": str(schema_version if schema_version is not None else SCHEMA_VERSION),
            "format": fmt,
            "source_name": source_name,
            "entry_count": "0",
        },
    )
    if with_source_zip:
        (dict_dir / "source.zip").write_bytes(b"PK\x03\x04 fake zip bytes")
    return dict_dir


@pytest.fixture
def tab_for_restore(test_config: AnkiMinerConfig, tmp_path: Path, qtbot):
    """SettingsTab with dicts_root scoped to tmp_path."""
    cfg = replace(
        test_config,
        dicts_root=tmp_path / "dicts",
        jmdict_path=tmp_path / "JMdict_e",
    )
    (tmp_path / "dicts").mkdir(parents=True, exist_ok=True)
    widget = SettingsTab(cfg)
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def test_restore_nothing_when_all_listed(tab_for_restore, monkeypatch):
    """Chain already lists the only on-disk dict — info dialog, no change."""
    tab = tab_for_restore
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(dicts_root, "dict-a", fmt="yomitan", source_name="Dict A")
    tab.dictionary_panel.set_chain((ChainEntry(kind="indexed", dict_id="dict-a", enabled=True),))

    info_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, body, *a, **kw: info_calls.append((title, body)) or 0,
    )

    config_changed_emissions: list[object] = []
    tab.config_changed.connect(config_changed_emissions.append)

    original_chain = tab.config.dictionary_chain
    # Panel chain is the real source of truth restore_unlisted reads/writes;
    # asserting on tab.config alone would pass even if set_chain mutated the
    # panel without persisting (the early-exit path must touch neither).
    panel_chain_before = tab.dictionary_panel.get_chain()
    tab._dict_import_flow.restore_unlisted()

    assert any(title == "Nothing to restore" for title, _ in info_calls), info_calls
    assert tab.config.dictionary_chain == original_chain
    assert tab.dictionary_panel.get_chain() == panel_chain_before
    assert config_changed_emissions == []


def test_restore_orphan_confirmed_inserts_before_jisho(tab_for_restore, monkeypatch):
    """Orphan on disk + user says Yes → inserted before jisho, config_changed once."""
    tab = tab_for_restore
    dicts_root = tab.config.dicts_root
    # jmdict-english is in the chain; orphan-dict is on disk but NOT in the chain.
    _make_dict_on_disk(dicts_root, "jmdict-english", fmt="jmdict", source_name="JMdict (English)")
    _make_dict_on_disk(dicts_root, "orphan-dict", fmt="yomitan", source_name="Orphan Dict")
    tab.dictionary_panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda parent, title, body, *a, **kw: QMessageBox.StandardButton.Yes,
    )

    config_changed_emissions: list[object] = []
    tab.config_changed.connect(config_changed_emissions.append)

    tab._dict_import_flow.restore_unlisted()

    new_chain = tab.config.dictionary_chain
    dict_ids = [e.dict_id for e in new_chain]

    # Orphan was added
    assert "orphan-dict" in dict_ids

    # Orphan appears before jisho
    orphan_idx = dict_ids.index("orphan-dict")
    jisho_positions = [i for i, e in enumerate(new_chain) if e.kind == "jisho"]
    assert jisho_positions, "jisho must remain in chain"
    assert all(
        orphan_idx < j for j in jisho_positions
    ), f"orphan at {orphan_idx} must precede jisho at {jisho_positions}"

    # Orphan entry is enabled
    orphan_entry = next(e for e in new_chain if e.dict_id == "orphan-dict")
    assert orphan_entry.enabled is True
    assert orphan_entry.kind == "indexed"

    # config_changed emitted exactly once
    assert len(config_changed_emissions) == 1


def test_restore_multiple_orphans_sorted_before_jisho(tab_for_restore, monkeypatch):
    """Several orphans are inserted as a block before jisho, ordered by dict_id."""
    tab = tab_for_restore
    dicts_root = tab.config.dicts_root
    # Seed out of alphabetical order to prove the result is sorted, not scan-order.
    _make_dict_on_disk(dicts_root, "z-dict", fmt="yomitan", source_name="Z Dict")
    _make_dict_on_disk(dicts_root, "a-dict", fmt="yomitan", source_name="A Dict")
    tab.dictionary_panel.set_chain((ChainEntry(kind="jisho", dict_id=None, enabled=True),))

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda parent, title, body, *a, **kw: QMessageBox.StandardButton.Yes,
    )

    tab._dict_import_flow.restore_unlisted()

    dict_ids = [e.dict_id for e in tab.config.dictionary_chain]
    jisho_idx = next(i for i, e in enumerate(tab.config.dictionary_chain) if e.kind == "jisho")
    # Both orphans added, sorted (a before z), and both ahead of jisho.
    assert dict_ids[:jisho_idx] == ["a-dict", "z-dict"]


def test_restore_orphan_declined_no_change(tab_for_restore, monkeypatch):
    """Orphan on disk + user says No → chain unchanged, no config_changed."""
    tab = tab_for_restore
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(dicts_root, "orphan-dict", fmt="yomitan", source_name="Orphan Dict")
    tab.dictionary_panel.set_chain((ChainEntry(kind="jisho", dict_id=None, enabled=True),))

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda parent, title, body, *a, **kw: QMessageBox.StandardButton.No,
    )

    config_changed_emissions: list[object] = []
    tab.config_changed.connect(config_changed_emissions.append)

    original_chain = tab.config.dictionary_chain
    panel_chain_before = tab.dictionary_panel.get_chain()
    tab._dict_import_flow.restore_unlisted()

    assert tab.config.dictionary_chain == original_chain
    assert tab.dictionary_panel.get_chain() == panel_chain_before
    assert config_changed_emissions == []


def test_restore_schema_mismatched_not_offered(tab_for_restore, monkeypatch):
    """A dict with wrong schema_version is not schema_ok — not surfaced as orphan."""
    tab = tab_for_restore
    dicts_root = tab.config.dicts_root
    # Write a dict with a future/unknown schema version (schema_ok=False)
    _make_dict_on_disk(
        dicts_root,
        "future-dict",
        fmt="yomitan",
        source_name="Future Dict",
        schema_version=999,
    )
    # Chain is empty (no indexed entries at all)
    tab.dictionary_panel.set_chain(())

    info_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, body, *a, **kw: info_calls.append((title, body)) or 0,
    )

    config_changed_emissions: list[object] = []
    tab.config_changed.connect(config_changed_emissions.append)

    original_chain = tab.config.dictionary_chain
    panel_chain_before = tab.dictionary_panel.get_chain()
    tab._dict_import_flow.restore_unlisted()

    # Should show "Nothing to restore" because schema_ok=False excludes the dict
    assert any(title == "Nothing to restore" for title, _ in info_calls), info_calls
    assert tab.config.dictionary_chain == original_chain
    assert tab.dictionary_panel.get_chain() == panel_chain_before
    assert config_changed_emissions == []

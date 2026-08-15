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

import anki_miner.gui.widgets.settings_tab as settings_tab_module
from anki_miner.config import (
    AnkiMinerConfig,
    AudioSourceEntry,
    ChainEntry,
    FreqEntry,
    PitchSourceEntry,
)
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.services.audio_packs import storage as audio_storage
from anki_miner.services.dictionary.storage import SCHEMA_VERSION, create_index, write_meta
from anki_miner.services.frequency import storage as frequency_storage
from anki_miner.services.pitch_accent import storage as pitch_storage


def _run_scan_sync(work, on_done, on_error):
    try:
        on_done(work())
    except Exception as exc:  # noqa: BLE001
        on_error(str(exc))


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
    widget._dict_import_flow._run_latest_scan = _run_scan_sync
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


@pytest.fixture
def tab_for_resource_restore(test_config: AnkiMinerConfig, tmp_path: Path, qtbot):
    cfg = replace(
        test_config,
        audio_packs_root=tmp_path / "audio_packs",
        freqs_root=tmp_path / "freqs",
        pitch_root=tmp_path / "pitch",
        expression_audio_chain=(),
        frequency_chain=(),
        pitch_chain=(),
    )
    widget = SettingsTab(cfg, suppress_optional_startup=True)
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

    new_chain = config_changed_emissions[0].dictionary_chain
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

    config_changed_emissions: list[object] = []
    tab.config_changed.connect(config_changed_emissions.append)
    tab._dict_import_flow.restore_unlisted()

    new_chain = config_changed_emissions[0].dictionary_chain
    dict_ids = [e.dict_id for e in new_chain]
    jisho_idx = next(i for i, e in enumerate(new_chain) if e.kind == "jisho")
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


@pytest.mark.parametrize("kind", ["audio", "frequency", "pitch"])
def test_restore_unlisted_resource_without_reimport(tab_for_resource_restore, tmp_path, monkeypatch, kind):
    tab = tab_for_resource_restore
    source_id = "retained-source"
    if kind == "audio":
        pack_dir = tmp_path / "original-audio"
        pack_dir.mkdir()
        db_path = tab.config.audio_packs_root / source_id / "index.sqlite"
        audio_storage.create_index(db_path)
        audio_storage.write_meta(
            db_path,
            {
                "schema_version": str(audio_storage.SCHEMA_VERSION),
                "pack_id": source_id,
                "source": "Retained Audio",
                "format": "ajt",
                "entry_count": "0",
                "pack_dir": str(pack_dir),
            },
        )
        panel = tab.audio_panel
        chain_attr = "expression_audio_chain"
    elif kind == "frequency":
        db_path = tab.config.freqs_root / source_id / "index.sqlite"
        frequency_storage.build_index(
            db_path,
            [("猫", None, 1, None)],
            {
                "schema_version": str(frequency_storage.SCHEMA_VERSION),
                "source_name": "Retained Frequency",
                "format": "csv",
                "entry_count": "1",
            },
        )
        panel = tab.frequency_panel
        chain_attr = "frequency_chain"
    else:
        db_path = tab.config.pitch_root / source_id / "index.sqlite"
        pitch_storage.build_index(
            db_path,
            [("ねこ", "猫", "1", "", "")],
            {
                "schema_version": str(pitch_storage.SCHEMA_VERSION),
                "source_name": "Retained Pitch",
                "format": "csv",
                "entry_count": "1",
            },
        )
        panel = tab.pitch_panel
        chain_attr = "pitch_chain"

    monkeypatch.setattr(
        settings_tab_module,
        "run_off_thread",
        lambda _parent, work, on_done, on_error: _run_scan_sync(work, on_done, on_error),
    )
    emissions: list[AnkiMinerConfig] = []
    tab.config_changed.connect(emissions.append)

    panel._restore_btn.click()

    assert len(emissions) == 1
    restored_chain = getattr(emissions[0], chain_attr)
    restored_ids = [getattr(entry, "pack_id", None) or getattr(entry, "source_id", None) for entry in restored_chain]
    assert restored_ids == [source_id]
    assert db_path.is_file()


@pytest.mark.parametrize("kind", ["audio", "frequency", "pitch"])
def test_delayed_restore_result_is_discarded_after_root_and_chain_change(
    tab_for_resource_restore,
    tmp_path,
    monkeypatch,
    kind,
):
    tab = tab_for_resource_restore
    source_id = "retained-source"
    existing_id = "current-chain-source"
    delayed: dict[str, object] = {}

    def hold_scan(_parent, work, on_done, on_error):
        delayed.update(work=work, on_done=on_done, on_error=on_error)

    monkeypatch.setattr(settings_tab_module, "run_off_thread", hold_scan)

    if kind == "audio":
        panel = tab.audio_panel
        root_attr = "audio_packs_root"
        chain_attr = "expression_audio_chain"
        new_root = tmp_path / "new-audio-packs"
        pack_dir = tmp_path / "original-audio"
        pack_dir.mkdir()
        db_path = tab.config.audio_packs_root / source_id / "index.sqlite"
        audio_storage.create_index(db_path)
        audio_storage.write_meta(
            db_path,
            {
                "schema_version": str(audio_storage.SCHEMA_VERSION),
                "pack_id": source_id,
                "source": "New Root Audio",
                "format": "ajt",
                "entry_count": "0",
                "pack_dir": str(pack_dir),
            },
        )
        new_chain = (AudioSourceEntry(kind="pack", pack_id=existing_id),)
    elif kind == "frequency":
        panel = tab.frequency_panel
        root_attr = "freqs_root"
        chain_attr = "frequency_chain"
        new_root = tmp_path / "new-freqs"
        db_path = tab.config.freqs_root / source_id / "index.sqlite"
        frequency_storage.build_index(
            db_path,
            [("猫", None, 1, None)],
            {
                "schema_version": str(frequency_storage.SCHEMA_VERSION),
                "source_name": "New Root Frequency",
                "format": "csv",
                "entry_count": "1",
            },
        )
        new_chain = (FreqEntry(existing_id),)
    else:
        panel = tab.pitch_panel
        root_attr = "pitch_root"
        chain_attr = "pitch_chain"
        new_root = tmp_path / "new-pitch"
        db_path = tab.config.pitch_root / source_id / "index.sqlite"
        pitch_storage.build_index(
            db_path,
            [("ねこ", "猫", "1", "", "")],
            {
                "schema_version": str(pitch_storage.SCHEMA_VERSION),
                "source_name": "New Root Pitch",
                "format": "csv",
                "entry_count": "1",
            },
        )
        new_chain = (PitchSourceEntry(existing_id),)

    panel._restore_btn.click()
    result = delayed["work"]()
    result_ids = [getattr(item, "pack_id", None) or getattr(item, "source_id", None) for item in result]
    assert result_ids == [source_id]

    emissions: list[AnkiMinerConfig] = []
    tab.config_changed.connect(emissions.append)
    tab.reload_from_config(replace(tab.config, **{root_attr: new_root, chain_attr: new_chain}))

    delayed["on_done"](result)

    assert emissions == []
    assert panel.get_chain() == new_chain


@pytest.mark.parametrize(
    ("kind", "expected_fix"),
    [("audio", "Re-import"), ("frequency", "Reimport All"), ("pitch", "Reimport All")],
)
def test_restore_says_so_when_it_finds_nothing(
    tab_for_resource_restore,
    tmp_path,
    monkeypatch,
    kind,
    expected_fix,
):
    """Silence here read as a dead button (the v2.10.0 report).

    ``unlisted()`` drops anything already chained AND anything schema-stale, so
    right after an index bump an empty result is the normal outcome for every
    user. The box also names the control that does repair a stale index, since
    that is what someone clicking Restore is usually trying to do.
    """
    tab = tab_for_resource_restore
    panel = {"audio": tab.audio_panel, "frequency": tab.frequency_panel, "pitch": tab.pitch_panel}[kind]

    monkeypatch.setattr(
        settings_tab_module,
        "run_off_thread",
        lambda _parent, work, on_done, on_error: _run_scan_sync(work, on_done, on_error),
    )
    info_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, body, *a, **kw: info_calls.append((title, body)) or 0,
    )
    emissions: list[AnkiMinerConfig] = []
    tab.config_changed.connect(emissions.append)

    panel._restore_btn.click()

    assert len(info_calls) == 1, info_calls
    title, body = info_calls[0]
    assert title == "Nothing to restore"
    assert expected_fix in body
    # The no-op path must touch neither the panel chain nor the config.
    assert emissions == []

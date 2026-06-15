"""Integration test for Issue #45: configurable dictionary storage root.

Seeds an indexed dictionary at a non-default path, builds a SettingsTab against
it, and asserts the chain panel reflects the on-disk dict scanned at the
configured ``dicts_root`` instead of the hardcoded ``~/.anki_miner/dicts``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig, ChainEntry  # noqa: E402
from anki_miner.gui.widgets.settings_tab import SettingsTab  # noqa: E402
from anki_miner.services.dictionary.storage import (  # noqa: E402
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)


def _seed_yomitan_dict(root: Path, dict_id: str, source_name: str) -> None:
    folder = root / dict_id
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "index.sqlite"
    create_index(db)
    bulk_insert(db, [DictRow(term="食べる", reading="たべる", content="<div>example</div>", sequence=1)])
    write_meta(
        db,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": source_name,
            "format": "yomitan",
            "entry_count": "1",
        },
    )


def test_settings_tab_scans_custom_dicts_root(qtbot, tmp_path):
    """SettingsTab pointed at a non-default dicts_root must surface dicts found
    there (not silently fall back to ~/.anki_miner/dicts)."""
    external = tmp_path / "external_ssd_dicts"
    external.mkdir()
    _seed_yomitan_dict(external, "custom-dict", source_name="My Custom Dict")

    config = replace(
        AnkiMinerConfig(),
        dicts_root=external,
        dictionary_chain=(
            ChainEntry(kind="indexed", dict_id="custom-dict", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=False),
        ),
    )

    widget = SettingsTab(config)
    qtbot.addWidget(widget)
    panel = widget.dictionary_panel
    assert panel.get_dicts_root() == external

    # Force the panel's registry cache to (re)scan the configured root.
    panel.refresh_registry()
    panel.set_chain(config.dictionary_chain)

    row = panel._row_widget(0)
    assert row is not None, "indexed row must render for the seeded dict"

    from PyQt6.QtWidgets import QLabel

    label_texts = [lbl.text() for lbl in row.findChildren(QLabel)]
    # source_name from meta surfaces — proves the registry scanned the
    # custom root rather than missing the dict and rendering "(missing)".
    assert any("My Custom Dict" in t for t in label_texts), label_texts
    assert not any("(missing)" in t for t in label_texts)

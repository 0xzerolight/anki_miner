"""What each registry ``load()`` found, and what it could not parse.

``load()`` runs at boot and again after every import, so its one-line inventory
is where "did my pack actually appear?" is answered — and where a slot that is
on disk but schema-stale is named before the chain silently drops it. The
per-slot ``Index meta invalid`` line covers the other half: a meta value the
parser swallowed into a ``0`` default, which otherwise looks identical to a
missing key.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from anki_miner.services.audio_packs.registry import AudioPackRegistry
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.services.pitch_accent.registry import PitchSourceRegistry


def _current_schema_version(family: str) -> int:
    from anki_miner.services.audio_packs.storage import SCHEMA_VERSION as AUDIO
    from anki_miner.services.dictionary.storage import SCHEMA_VERSION as DICTIONARY
    from anki_miner.services.frequency.storage import SCHEMA_VERSION as FREQUENCY
    from anki_miner.services.pitch_accent.storage import SCHEMA_VERSION as PITCH

    return {"audio": AUDIO, "dictionary": DICTIONARY, "frequency": FREQUENCY, "pitch": PITCH}[family]


def _make_slot(root: Path, slot_id: str, meta: dict[str, str]) -> Path:
    """Create ``<root>/<slot_id>/index.sqlite`` carrying exactly *meta*."""
    slot = root / slot_id
    slot.mkdir(parents=True)
    conn = sqlite3.connect(slot / "index.sqlite")
    try:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", sorted(meta.items()))
        conn.commit()
    finally:
        conn.close()
    return slot


def _messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == level]


_FAMILIES = {
    "dictionary": (DictionaryRegistry, "anki_miner.services.dictionary.registry"),
    "frequency": (FrequencySourceRegistry, "anki_miner.services.frequency.registry"),
    "pitch": (PitchSourceRegistry, "anki_miner.services.pitch_accent.registry"),
    "audio": (AudioPackRegistry, "anki_miner.services.audio_packs.registry"),
}


class TestResourceInventory:
    @pytest.mark.parametrize("family", sorted(_FAMILIES))
    def test_load_reports_one_inventory_line_naming_the_stale_slots(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, family: str
    ):
        registry_cls, logger_name = _FAMILIES[family]
        current = str(_current_schema_version(family))
        _make_slot(tmp_path, "good", {"schema_version": current})
        _make_slot(tmp_path, "bad", {"schema_version": "99"})

        with caplog.at_level(logging.INFO, logger=logger_name):
            registry_cls(tmp_path).load()

        inventory = [m for m in _messages(caplog, logging.INFO) if m.startswith("Resource inventory: ")]
        assert len(inventory) == 1
        (message,) = inventory
        assert f"family={family}" in message
        assert f"root={tmp_path}" in message
        assert "count=2" in message
        assert "schema_bad=bad" in message
        assert "ids=bad,good" in message

    def test_an_empty_root_still_reports_its_inventory(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """A root that scanned to nothing is the interesting case, not the
        boring one: it is the difference between "no packs installed" and "the
        packs root moved"."""
        with caplog.at_level(logging.INFO, logger="anki_miner.services.dictionary.registry"):
            DictionaryRegistry(tmp_path / "absent").load()

        (message,) = [m for m in _messages(caplog, logging.INFO) if m.startswith("Resource inventory: ")]
        assert "count=0" in message
        assert "ids=-" in message


class TestIndexMetaInvalid:
    @pytest.mark.parametrize("family", sorted(_FAMILIES))
    def test_an_unparseable_schema_version_is_named_not_silently_zeroed(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, family: str
    ):
        registry_cls, logger_name = _FAMILIES[family]
        slot = _make_slot(tmp_path, "slot", {"schema_version": "x"})

        with caplog.at_level(logging.WARNING, logger=logger_name):
            registry_cls(tmp_path).load()

        invalid = [m for m in _messages(caplog, logging.WARNING) if m.startswith("Index meta invalid: ")]
        assert len(invalid) == 1
        (message,) = invalid
        assert f"dir={slot}" in message
        assert "key=schema_version" in message
        assert "value=x" in message

    @pytest.mark.parametrize("family", sorted(_FAMILIES))
    def test_an_unparseable_entry_count_is_named_too(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, family: str
    ):
        registry_cls, logger_name = _FAMILIES[family]
        _make_slot(
            tmp_path,
            "slot",
            {"schema_version": str(_current_schema_version(family)), "entry_count": "lots"},
        )

        with caplog.at_level(logging.WARNING, logger=logger_name):
            registry_cls(tmp_path).load()

        invalid = [m for m in _messages(caplog, logging.WARNING) if m.startswith("Index meta invalid: ")]
        assert len(invalid) == 1
        assert "key=entry_count" in invalid[0]
        assert "value=lots" in invalid[0]

    @pytest.mark.parametrize("family", sorted(_FAMILIES))
    def test_a_well_formed_slot_reports_nothing_invalid(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, family: str
    ):
        registry_cls, logger_name = _FAMILIES[family]
        _make_slot(
            tmp_path,
            "slot",
            {"schema_version": str(_current_schema_version(family)), "entry_count": "7"},
        )

        with caplog.at_level(logging.WARNING, logger=logger_name):
            registry_cls(tmp_path).load()

        assert not [m for m in _messages(caplog, logging.WARNING) if m.startswith("Index meta invalid: ")]

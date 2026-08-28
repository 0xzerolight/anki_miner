"""Every resource family reports a language; a pre-Stage-0 index reports "ja"."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.audio_packs.registry import AudioPackRegistry
from anki_miner.services.audio_packs.storage import SCHEMA_VERSION as PACK_SCHEMA
from anki_miner.services.audio_packs.storage import create_index as pack_create_index
from anki_miner.services.audio_packs.storage import write_meta as pack_write_meta
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.dictionary.storage import SCHEMA_VERSION as DICT_SCHEMA
from anki_miner.services.dictionary.storage import create_index as dict_create_index
from anki_miner.services.dictionary.storage import write_meta as dict_write_meta
from anki_miner.services.frequency import storage as freq_storage
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.services.pitch_accent import storage as pitch_storage
from anki_miner.services.pitch_accent.registry import PitchSourceRegistry


def _dict_slot(root: Path, slot_id: str, language: str | None) -> None:
    db = root / slot_id / "index.sqlite"
    dict_create_index(db)
    meta = {
        "schema_version": str(DICT_SCHEMA),
        "format": "yomitan",
        "source_name": slot_id,
        "entry_count": "1",
    }
    if language is not None:
        meta["language"] = language
    dict_write_meta(db, meta)


def _freq_slot(root: Path, slot_id: str, language: str | None) -> None:
    meta = {
        "schema_version": str(freq_storage.SCHEMA_VERSION),
        "format": "csv",
        "source_name": slot_id,
        "entry_count": "1",
    }
    if language is not None:
        meta["language"] = language
    freq_storage.build_index(root / slot_id / "index.sqlite", [("猫", None, 5, None)], meta)


def _pitch_slot(root: Path, slot_id: str, language: str | None) -> None:
    meta = {
        "schema_version": str(pitch_storage.SCHEMA_VERSION),
        "format": "csv",
        "source_name": slot_id,
        "entry_count": "1",
    }
    if language is not None:
        meta["language"] = language
    pitch_storage.build_index(root / slot_id / "index.sqlite", [("ねこ", "猫", "1", "", "")], meta)


def _pack_slot(root: Path, slot_id: str, language: str | None) -> None:
    slot = root / slot_id
    db = slot / "index.sqlite"
    pack_create_index(db)
    meta = {
        "pack_id": slot_id,
        "source": slot_id,
        "format": "ajt",
        "entry_count": "1",
        "schema_version": str(PACK_SCHEMA),
        "pack_dir": str(slot),
    }
    if language is not None:
        meta["language"] = language
    pack_write_meta(db, meta)


def test_absent_language_key_reads_as_ja(tmp_path: Path):
    _dict_slot(tmp_path / "dicts", "legacy", None)
    _freq_slot(tmp_path / "freqs", "legacy", None)
    _pitch_slot(tmp_path / "pitch", "legacy", None)
    _pack_slot(tmp_path / "packs", "legacy", None)

    dicts = DictionaryRegistry(tmp_path / "dicts")
    dicts.load()
    freqs = FrequencySourceRegistry(tmp_path / "freqs")
    freqs.load()
    pitches = PitchSourceRegistry(tmp_path / "pitch")
    pitches.load()
    packs = AudioPackRegistry(tmp_path / "packs")
    packs.load()

    assert dicts.get("legacy").language == "ja"
    assert freqs.get("legacy").language == "ja"
    assert pitches.get("legacy").language == "ja"
    assert packs.packs["legacy"].language == "ja"


def test_stamped_language_is_reported(tmp_path: Path):
    _dict_slot(tmp_path / "dicts", "cedict", "zh")
    _freq_slot(tmp_path / "freqs", "subtlex", "zh")
    _pitch_slot(tmp_path / "pitch", "kanjium", "ja")
    _pack_slot(tmp_path / "packs", "kopack", "ko")

    dicts = DictionaryRegistry(tmp_path / "dicts")
    dicts.load()
    freqs = FrequencySourceRegistry(tmp_path / "freqs")
    freqs.load()
    pitches = PitchSourceRegistry(tmp_path / "pitch")
    pitches.load()
    packs = AudioPackRegistry(tmp_path / "packs")
    packs.load()

    assert dicts.get("cedict").language == "zh"
    assert freqs.get("subtlex").language == "zh"
    assert pitches.get("kanjium").language == "ja"
    assert packs.packs["kopack"].language == "ko"

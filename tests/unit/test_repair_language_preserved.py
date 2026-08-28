"""A repair must not rewrite a non-ja index's language back to "ja"."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from anki_miner.services._sqlite_index import meta_language, read_slot_language
from anki_miner.services.audio_packs.importer import import_audio_pack, repair_audio_pack
from anki_miner.services.audio_packs.storage import read_meta_cached as pack_meta
from anki_miner.services.dictionary.importers.yomitan_importer import (
    import_yomitan_zip,
    repair_yomitan_zip,
)
from anki_miner.services.dictionary.storage import read_meta as dict_meta
from anki_miner.services.frequency import storage as freq_storage
from anki_miner.services.frequency.source_importer import (
    import_frequency_source,
    repair_frequency_source,
)
from anki_miner.services.pitch_accent import storage as pitch_storage
from anki_miner.services.pitch_accent.source_importer import (
    import_pitch_source,
    repair_pitch_source,
)
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip
from tests.unit.test_audio_pack_registry import _make_ajt_pack


def test_read_slot_language_defaults_to_ja_on_a_broken_slot(tmp_path: Path):
    slot = tmp_path / "broken"
    slot.mkdir()
    assert read_slot_language(slot) == "ja"
    (slot / "index.sqlite").write_bytes(b"not a database")
    assert read_slot_language(slot) == "ja"


def test_read_slot_language_falls_back_to_sqlite_without_a_sidecar(tmp_path: Path):
    csv_path = tmp_path / "f.csv"
    csv_path.write_text("term,rank\n猫,5\n", encoding="utf-8")
    dest = tmp_path / "freqs"
    import_frequency_source(csv_path, dest, source_id="zh-freq", language="zh")
    (dest / "zh-freq" / "meta.json").unlink()
    assert read_slot_language(dest / "zh-freq") == "zh"


def test_dictionary_repair_keeps_zh(tmp_path: Path):
    zip_path = build_yomitan_zip(tmp_path / "src" / "d.zip")
    dest = tmp_path / "dicts"
    import_yomitan_zip(zip_path, dest, dict_id="zh-dict", language="zh")
    repair_yomitan_zip(zip_path, dest, dict_id="zh-dict")
    assert meta_language(dict_meta(dest / "zh-dict" / "index.sqlite")) == "zh"


def test_frequency_repair_keeps_zh(tmp_path: Path):
    csv_path = tmp_path / "f.csv"
    csv_path.write_text("term,rank\n猫,5\n", encoding="utf-8")
    dest = tmp_path / "freqs"
    import_frequency_source(csv_path, dest, source_id="zh-freq", language="zh")
    repair_frequency_source(csv_path, dest, source_id="zh-freq", source_name="SUBTLEX")
    assert meta_language(freq_storage.read_meta_cached(dest / "zh-freq" / "index.sqlite")) == "zh"


def test_pitch_repair_keeps_ja_and_ko(tmp_path: Path):
    csv_path = tmp_path / "p.csv"
    csv_path.write_text("ねこ,猫,1\n", encoding="utf-8")
    dest = tmp_path / "pitch"
    import_pitch_source(csv_path, dest, source_id="ja-pitch")
    import_pitch_source(csv_path, dest, source_id="ko-pitch", language="ko")
    repair_pitch_source(csv_path, dest, source_id="ja-pitch", source_name="Kanjium")
    repair_pitch_source(csv_path, dest, source_id="ko-pitch", source_name="KO")
    assert meta_language(pitch_storage.read_meta_cached(dest / "ja-pitch" / "index.sqlite")) == "ja"
    assert meta_language(pitch_storage.read_meta_cached(dest / "ko-pitch" / "index.sqlite")) == "ko"


def test_audio_pack_repair_keeps_ko_even_when_the_index_is_corrupt(tmp_path: Path):
    pack_dir = _make_ajt_pack(tmp_path / "pack")
    dest = tmp_path / "packs"
    import_audio_pack(pack_dir, dest, pack_id="ko-pack", language="ko")
    # Corrupt the index but leave the sidecar: this is the branch that quarantines
    # the slot, and the language must still survive the rebuild.
    with sqlite3.connect(dest / "ko-pack" / "index.sqlite") as conn:
        conn.execute("DROP TABLE entries")
    repair_audio_pack(pack_dir, dest, pack_id="ko-pack")
    assert meta_language(pack_meta(dest / "ko-pack" / "index.sqlite")) == "ko"

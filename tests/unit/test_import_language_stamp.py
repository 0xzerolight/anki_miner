"""Imports stamp meta["language"]; the default keeps ja byte-identical."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services._sqlite_index import meta_language
from anki_miner.services.audio_packs.importer import import_audio_pack
from anki_miner.services.audio_packs.storage import read_meta_cached as pack_meta
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.storage import read_meta as dict_meta
from anki_miner.services.frequency import storage as freq_storage
from anki_miner.services.frequency.source_importer import import_frequency_source
from anki_miner.services.pitch_accent import storage as pitch_storage
from anki_miner.services.pitch_accent.source_importer import import_pitch_source
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip
from tests.unit.test_audio_pack_registry import _make_ajt_pack


def test_yomitan_dict_stamps_language(tmp_path: Path):
    # The stamped language also picks the import-side key folding, which the
    # real zh profile now supplies.
    zip_path = build_yomitan_zip(tmp_path / "src" / "d.zip")
    dest = tmp_path / "dicts"
    ja = import_yomitan_zip(zip_path, dest, dict_id="ja-dict")
    zh = import_yomitan_zip(zip_path, dest, dict_id="zh-dict", language="zh")
    assert meta_language(dict_meta(dest / ja.dict_id / "index.sqlite")) == "ja"
    assert meta_language(dict_meta(dest / zh.dict_id / "index.sqlite")) == "zh"


def test_frequency_source_stamps_language(tmp_path: Path):
    csv_path = tmp_path / "f.csv"
    csv_path.write_text("term,rank\n猫,5\n", encoding="utf-8")
    dest = tmp_path / "freqs"
    import_frequency_source(csv_path, dest, source_id="ja-freq")
    import_frequency_source(csv_path, dest, source_id="zh-freq", language="zh")
    assert meta_language(freq_storage.read_meta_cached(dest / "ja-freq" / "index.sqlite")) == "ja"
    assert meta_language(freq_storage.read_meta_cached(dest / "zh-freq" / "index.sqlite")) == "zh"


def test_pitch_source_stamps_language(tmp_path: Path):
    csv_path = tmp_path / "p.csv"
    csv_path.write_text("ねこ,猫,1\n", encoding="utf-8")
    dest = tmp_path / "pitch"
    import_pitch_source(csv_path, dest, source_id="ja-pitch")
    import_pitch_source(csv_path, dest, source_id="ko-pitch", language="ko")
    assert meta_language(pitch_storage.read_meta_cached(dest / "ja-pitch" / "index.sqlite")) == "ja"
    assert meta_language(pitch_storage.read_meta_cached(dest / "ko-pitch" / "index.sqlite")) == "ko"


def test_audio_pack_stamps_language(tmp_path: Path):
    pack_dir = _make_ajt_pack(tmp_path / "pack")
    dest = tmp_path / "packs"
    import_audio_pack(pack_dir, dest, pack_id="ja-pack")
    import_audio_pack(pack_dir, dest, pack_id="ko-pack", language="ko")
    assert meta_language(pack_meta(dest / "ja-pack" / "index.sqlite")) == "ja"
    assert meta_language(pack_meta(dest / "ko-pack" / "index.sqlite")) == "ko"

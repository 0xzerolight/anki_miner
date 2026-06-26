"""Tests for FrequencySourceRegistry (disk scan + chain assembly)."""

from __future__ import annotations

from pathlib import Path

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.services.frequency import storage
from anki_miner.services.frequency.providers.indexed_freq_provider import (
    IndexedFreqProvider,
)
from anki_miner.services.frequency.registry import (
    FreqSourceMeta,
    FrequencySourceRegistry,
)


def _build_source(
    root: Path, source_id: str, rows: list[storage.FreqRow], *, schema_version: int | None = None
) -> None:
    db_path = root / source_id / "index.sqlite"
    meta = {
        "schema_version": str(storage.SCHEMA_VERSION if schema_version is None else schema_version),
        "format": "csv",
        "source_name": source_id.upper(),
        "entry_count": str(len(rows)),
    }
    storage.build_index(db_path, rows, meta)


def test_load_finds_sources(tmp_path: Path):
    _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    _build_source(tmp_path, "bccwj", [("犬", "いぬ", 200), ("猫", "ねこ", 50)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()

    jpdb = reg.get("jpdb")
    assert isinstance(jpdb, FreqSourceMeta)
    assert jpdb.source_id == "jpdb"
    assert jpdb.source_name == "JPDB"
    assert jpdb.format == "csv"
    assert jpdb.entry_count == 1
    assert jpdb.schema_ok is True
    assert jpdb.db_path == tmp_path / "jpdb" / "index.sqlite"

    bccwj = reg.get("bccwj")
    assert bccwj is not None
    assert bccwj.entry_count == 2


def test_get_missing_returns_none(tmp_path: Path):
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    assert reg.get("ghost") is None


def test_load_skips_dir_without_index(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    assert reg.get("empty") is None
    assert reg.get("jpdb") is not None


def test_load_marks_schema_mismatch(tmp_path: Path):
    _build_source(tmp_path, "old", [("猫", "ねこ", 100)], schema_version=storage.SCHEMA_VERSION + 99)
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("old")
    assert meta is not None
    assert meta.schema_ok is False


def test_load_on_missing_root_is_empty(tmp_path: Path):
    reg = FrequencySourceRegistry(tmp_path / "nonexistent")
    reg.load()
    assert reg.get("anything") is None


def test_unlisted_excludes_chained_and_bad_schema(tmp_path: Path):
    _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    _build_source(tmp_path, "bccwj", [("犬", "いぬ", 200)])
    _build_source(tmp_path, "old", [("生", "せい", 80)], schema_version=storage.SCHEMA_VERSION + 99)
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()

    config = AnkiMinerConfig(frequency_chain=(FreqEntry(source_id="jpdb"),))
    unlisted = reg.unlisted(config)
    # jpdb is chained -> excluded; old has bad schema -> excluded; only bccwj.
    assert [m.source_id for m in unlisted] == ["bccwj"]


def test_unlisted_excludes_disabled_chained(tmp_path: Path):
    # A source referenced by a DISABLED chain entry is still "listed".
    _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    _build_source(tmp_path, "bccwj", [("犬", "いぬ", 200)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()

    config = AnkiMinerConfig(
        frequency_chain=(
            FreqEntry(source_id="jpdb", enabled=False),
            FreqEntry(source_id="bccwj"),
        )
    )
    assert reg.unlisted(config) == []


def test_unlisted_sorted_by_source_id(tmp_path: Path):
    _build_source(tmp_path, "zzz", [("猫", "ねこ", 100)])
    _build_source(tmp_path, "aaa", [("犬", "いぬ", 200)])
    _build_source(tmp_path, "mmm", [("生", "せい", 80)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    config = AnkiMinerConfig()
    assert [m.source_id for m in reg.unlisted(config)] == ["aaa", "mmm", "zzz"]


def test_build_sources_chain_order_and_enabled(tmp_path: Path):
    _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    _build_source(tmp_path, "bccwj", [("犬", "いぬ", 200)])
    _build_source(tmp_path, "novel", [("生", "せい", 80)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()

    config = AnkiMinerConfig(
        frequency_chain=(
            FreqEntry(source_id="bccwj"),
            FreqEntry(source_id="jpdb", enabled=False),  # disabled -> skipped
            FreqEntry(source_id="novel"),
        )
    )
    sources = reg.build_sources(config)
    assert all(isinstance(s, IndexedFreqProvider) for s in sources)
    # Order preserved; disabled jpdb dropped.
    assert [s.source_id for s in sources] == ["bccwj", "novel"]
    # build_sources must NOT call .load() (caller does).
    assert all(s.is_available() is False for s in sources)


def test_build_sources_skips_missing_on_disk(tmp_path: Path):
    _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    config = AnkiMinerConfig(
        frequency_chain=(
            FreqEntry(source_id="ghost"),  # not on disk
            FreqEntry(source_id="jpdb"),
        )
    )
    sources = reg.build_sources(config)
    assert [s.source_id for s in sources] == ["jpdb"]


def test_build_sources_skips_schema_mismatch(tmp_path: Path):
    _build_source(tmp_path, "old", [("猫", "ねこ", 100)], schema_version=storage.SCHEMA_VERSION + 99)
    _build_source(tmp_path, "jpdb", [("犬", "いぬ", 200)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    config = AnkiMinerConfig(
        frequency_chain=(
            FreqEntry(source_id="old"),
            FreqEntry(source_id="jpdb"),
        )
    )
    sources = reg.build_sources(config)
    assert [s.source_id for s in sources] == ["jpdb"]


def test_build_sources_uses_source_name_as_display(tmp_path: Path):
    _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    config = AnkiMinerConfig(frequency_chain=(FreqEntry(source_id="jpdb"),))
    sources = reg.build_sources(config)
    assert sources[0].name == "JPDB"

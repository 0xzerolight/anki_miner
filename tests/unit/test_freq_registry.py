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
from tests.unit.test_freq_storage import build_v1_index


def _build_source(
    root: Path,
    source_id: str,
    rows: list[tuple],
    *,
    schema_version: int | None = None,
    is_categorical: str | None = None,
) -> None:
    db_path = root / source_id / "index.sqlite"
    meta = {
        "schema_version": str(storage.SCHEMA_VERSION if schema_version is None else schema_version),
        "format": "csv",
        "source_name": source_id.upper(),
        "entry_count": str(len(rows)),
    }
    if is_categorical is not None:
        meta["is_categorical"] = is_categorical
    padded: list[storage.FreqRow] = [row if len(row) == 4 else (*row, None) for row in rows]
    storage.build_index(db_path, padded, meta)


def test_is_categorical_round_trips(tmp_path: Path):
    _build_source(tmp_path, "jlpt", [("猫", None, storage.CATEGORICAL_RANK)], is_categorical="1")
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("jlpt")
    assert meta is not None
    assert meta.is_categorical is True


def test_is_categorical_zero_reads_false(tmp_path: Path):
    # Meta values are strings: bool("0") would be truthy, so the registry must
    # compare == "1". A stored "0" reads back False.
    _build_source(tmp_path, "num", [("猫", "ねこ", 100)], is_categorical="0")
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("num")
    assert meta is not None
    assert meta.is_categorical is False


def test_is_categorical_absent_defaults_false(tmp_path: Path):
    _build_source(tmp_path, "legacy", [("猫", "ねこ", 100)])  # no is_categorical key
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("legacy")
    assert meta is not None
    assert meta.is_categorical is False


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
    assert jpdb.version == storage.SCHEMA_VERSION
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


def test_v1_source_is_schema_ok_but_version_below_latest(tmp_path: Path):
    # A real v1 index (no display_value column) stays loadable after the 1->2
    # bump; schema_ok is True (loadable), decoupled from is-latest.
    build_v1_index(tmp_path / "old" / "index.sqlite", [("猫", "ねこ", 100)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("old")
    assert meta is not None
    assert meta.version == 1
    assert meta.schema_ok is True
    # The optional out-of-date notice keys on version < SCHEMA_VERSION, which
    # fires here — while schema_ok (loadable) does NOT distinguish v1 from v2.
    assert meta.version < storage.SCHEMA_VERSION


def test_v2_source_version_is_latest(tmp_path: Path):
    _build_source(tmp_path, "new", [("猫", "ねこ", 100)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("new")
    assert meta is not None
    assert meta.version == storage.SCHEMA_VERSION
    assert meta.schema_ok is True
    assert not (meta.version < storage.SCHEMA_VERSION)  # notice does NOT fire for v2


def test_future_version_rejected(tmp_path: Path):
    _build_source(tmp_path, "future", [("猫", "ねこ", 100)], schema_version=storage.SCHEMA_VERSION + 1)
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("future")
    assert meta is not None
    assert meta.version == storage.SCHEMA_VERSION + 1
    assert meta.schema_ok is False  # unknown newer schema — not loadable


def test_v1_index_included_and_read_via_build_sources_load_lookup(tmp_path: Path):
    # Regression for the traced zero-card failure: the build_sources schema drop
    # runs BEFORE provider.load(), so both seams must accept v1. If either drops
    # it, the v1 source vanishes and max_frequency_rank filters every word out.
    build_v1_index(
        tmp_path / "old" / "index.sqlite",
        [("猫", "ねこ", 100), ("犬", "いぬ", 5000)],
    )
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    config = AnkiMinerConfig(frequency_chain=(FreqEntry(source_id="old"),))

    providers = reg.build_sources(config)
    assert [p.source_id for p in providers] == ["old"]  # NOT dropped at build_sources
    provider = providers[0]
    assert provider.load() is True  # provider.load accepts v1 too

    # Nonzero, correct filtered set: 猫 (rank 100) passes max_frequency_rank=1000;
    # 犬 (rank 5000) does not — identical to pre-bump v1 behavior.
    max_rank = 1000
    passed = [t for t in ("猫", "犬") if (r := provider.lookup(t)) is not None and r <= max_rank]
    assert passed == ["猫"]
    assert provider.lookup_detail("猫") == (100, None)  # display absent on v1


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

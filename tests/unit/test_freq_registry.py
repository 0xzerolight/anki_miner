"""Tests for FrequencySourceRegistry (disk scan + chain assembly)."""

from __future__ import annotations

import sqlite3
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


def test_null_sqlite_scalars_degrade_to_defaults(tmp_path: Path):
    _build_source(tmp_path, "broken-meta", [("猫", "ねこ", 1)])
    db = tmp_path / "broken-meta" / "index.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meta SET value = NULL WHERE key IN ('schema_version', 'entry_count', 'source_name')")
    (db.parent / "meta.json").unlink()

    reg = FrequencySourceRegistry(tmp_path)
    reg.load()

    meta = reg.get("broken-meta")
    assert meta is not None
    assert meta.entry_count == 0
    assert meta.source_name == "broken-meta"
    assert meta.schema_ok is False


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


def test_backup_dir_not_enumerated_as_resource(tmp_path: Path):
    _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    _build_source(tmp_path, "jpdb.bak-20260721000000000000", [("猫", "ねこ", 100)])
    _build_source(tmp_path, "jpdb.tomb-20260721000000000001", [("猫", "ねこ", 100)])

    reg = FrequencySourceRegistry(tmp_path)
    reg.load()

    assert reg.get("jpdb") is not None
    assert reg.get("jpdb.bak-20260721000000000000") is None
    assert reg.get("jpdb.tomb-20260721000000000001") is None
    assert (tmp_path / "jpdb.bak-20260721000000000000").is_dir()
    assert (tmp_path / "jpdb.tomb-20260721000000000001").is_dir()


def test_staging_dir_not_enumerated_as_resource(tmp_path: Path):
    _build_source(tmp_path, ".staging-orphan", [("猫", "ねこ", 100)])

    reg = FrequencySourceRegistry(tmp_path)
    reg.load()

    assert reg.get(".staging-orphan") is None
    assert (tmp_path / ".staging-orphan").is_dir()


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


def test_v1_source_requires_reimport(tmp_path: Path):
    build_v1_index(tmp_path / "old" / "index.sqlite", [("猫", "ねこ", 100)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("old")
    assert meta is not None
    assert meta.version == 1
    assert meta.schema_ok is False
    assert meta.version < storage.SCHEMA_VERSION


def test_v2_source_requires_reimport(tmp_path: Path):
    _build_source(tmp_path, "old", [("猫", "ねこ", 100)], schema_version=2)
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("old")
    assert meta is not None
    assert meta.version == 2
    assert meta.schema_ok is False
    assert meta.version < storage.SCHEMA_VERSION


def test_current_source_version_is_schema_ok(tmp_path: Path):
    _build_source(tmp_path, "current", [("猫", "ねこ", 100)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("current")
    assert meta is not None
    assert meta.version == storage.SCHEMA_VERSION
    assert meta.schema_ok is True


def test_future_version_rejected(tmp_path: Path):
    _build_source(tmp_path, "future", [("猫", "ねこ", 100)], schema_version=storage.SCHEMA_VERSION + 1)
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    meta = reg.get("future")
    assert meta is not None
    assert meta.version == storage.SCHEMA_VERSION + 1
    assert meta.schema_ok is False  # unknown newer schema — not loadable


def test_v2_index_excluded_before_provider_load(tmp_path: Path):
    _build_source(tmp_path, "old", [("猫", "ねこ", 100)], schema_version=2)
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    config = AnkiMinerConfig(frequency_chain=(FreqEntry(source_id="old"),))

    meta = reg.get("old")
    assert meta is not None
    assert meta.schema_ok is False
    assert reg.build_sources(config) == []


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


# --- The reimport surfaces' single source of truth (schema-bump migration) ---
#
# Every reimport surface - the settings row button, the startup prompt, the
# pre-run gate, the System Health row - keys on stale_enabled. What it must NOT
# report is as load-bearing as what it must: frequency is optional, so a source
# the user never configured, disabled, or deleted from disk can never gate a run.


def test_stale_enabled_reports_only_present_but_stale(tmp_path: Path):
    _build_source(tmp_path, "old", [("猫", "ねこ", 100)], schema_version=2)
    _build_source(tmp_path, "current", [("犬", "いぬ", 200)])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    config = AnkiMinerConfig(
        freqs_root=tmp_path,
        frequency_chain=(FreqEntry(source_id="old"), FreqEntry(source_id="current")),
    )

    assert [m.source_id for m in reg.stale_enabled(config)] == ["old"]


def test_stale_enabled_ignores_disabled_and_absent_entries(tmp_path: Path):
    """A deliberate deletion and an unticked row are not upgrade damage.

    Neither has a persisted copy the app could rebuild from either way, so
    reporting them would nag about something no button here can fix.
    """
    _build_source(tmp_path, "off", [("猫", "ねこ", 100)], schema_version=2)
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    config = AnkiMinerConfig(
        freqs_root=tmp_path,
        frequency_chain=(
            FreqEntry(source_id="off", enabled=False),
            FreqEntry(source_id="deleted-from-disk"),
        ),
    )

    assert reg.stale_enabled(config) == []


def test_stale_enabled_empty_for_unconfigured_chain(tmp_path: Path):
    _build_source(tmp_path, "old", [("猫", "ねこ", 100)], schema_version=2)
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()

    assert reg.stale_enabled(AnkiMinerConfig(freqs_root=tmp_path)) == []


def test_usable_enabled_requires_current_schema_and_entries(tmp_path: Path):
    _build_source(tmp_path, "good", [("猫", "ねこ", 100)])
    _build_source(tmp_path, "stale", [("犬", "いぬ", 200)], schema_version=2)
    _build_source(tmp_path, "empty", [])
    reg = FrequencySourceRegistry(tmp_path)
    reg.load()
    config = AnkiMinerConfig(
        freqs_root=tmp_path,
        frequency_chain=(
            FreqEntry(source_id="good"),
            FreqEntry(source_id="stale"),
            FreqEntry(source_id="empty"),
            FreqEntry(source_id="gone"),
        ),
    )

    assert [m.source_id for m in reg.usable_enabled(config)] == ["good"]

"""Tests for the pitch source registry (scan, unlisted, build_sources)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, PitchSourceEntry
from anki_miner.services.pitch_accent import storage
from anki_miner.services.pitch_accent.registry import PitchSourceRegistry


def _make_source(root: Path, source_id: str, *, schema_version: int | None = None, name: str | None = None) -> None:
    db = root / source_id / "index.sqlite"
    storage.build_index(
        db,
        [("ねこ", "猫", "1", "", "")],
        {
            "schema_version": str(schema_version if schema_version is not None else storage.SCHEMA_VERSION),
            "format": "csv",
            "source_name": name or source_id,
            "source_revision": "",
            "import_date": "2026-01-01T00:00:00+00:00",
            "entry_count": "1",
        },
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "pitch"


class TestScan:
    def test_load_discovers_sources(self, root: Path) -> None:
        _make_source(root, "alpha", name="Alpha")
        _make_source(root, "beta")
        registry = PitchSourceRegistry(root)
        registry.load()
        meta = registry.get("alpha")
        assert meta is not None
        assert meta.source_name == "Alpha"
        assert meta.schema_ok is True
        assert meta.entry_count == 1
        assert registry.get("beta") is not None
        assert registry.get("missing") is None

    def test_missing_root_yields_empty(self, root: Path) -> None:
        registry = PitchSourceRegistry(root)
        registry.load()
        assert registry.get("anything") is None

    def test_unsupported_version_flagged_not_ok(self, root: Path) -> None:
        _make_source(root, "old", schema_version=99)
        registry = PitchSourceRegistry(root)
        registry.load()
        meta = registry.get("old")
        assert meta is not None
        assert meta.schema_ok is False
        assert meta.version == 99

    def test_generated_artifacts_skipped(self, root: Path) -> None:
        _make_source(root, "good")
        _make_source(root, "good.bak-123")
        registry = PitchSourceRegistry(root)
        registry.load()
        assert registry.get("good") is not None
        assert registry.get("good.bak-123") is None


class TestUnlisted:
    def test_unlisted_excludes_chained_and_disabled(self, root: Path) -> None:
        _make_source(root, "chained")
        _make_source(root, "disabled")
        _make_source(root, "orphan")
        cfg = replace(
            AnkiMinerConfig(),
            pitch_root=root,
            pitch_chain=(
                PitchSourceEntry("chained"),
                PitchSourceEntry("disabled", enabled=False),
            ),
        )
        registry = PitchSourceRegistry(root)
        registry.load()
        assert [m.source_id for m in registry.unlisted(cfg)] == ["orphan"]


class TestBuildSources:
    def test_chain_order_preserved(self, root: Path) -> None:
        _make_source(root, "b")
        _make_source(root, "a")
        cfg = replace(
            AnkiMinerConfig(),
            pitch_root=root,
            pitch_chain=(PitchSourceEntry("b"), PitchSourceEntry("a")),
        )
        registry = PitchSourceRegistry(root)
        registry.load()
        assert [p.source_id for p in registry.build_sources(cfg)] == ["b", "a"]

    def test_disabled_missing_and_stale_dropped(self, root: Path) -> None:
        _make_source(root, "ok")
        _make_source(root, "stale", schema_version=99)
        cfg = replace(
            AnkiMinerConfig(),
            pitch_root=root,
            pitch_chain=(
                PitchSourceEntry("ok"),
                PitchSourceEntry("off", enabled=False),
                PitchSourceEntry("gone"),
                PitchSourceEntry("stale"),
            ),
        )
        registry = PitchSourceRegistry(root)
        registry.load()
        assert [p.source_id for p in registry.build_sources(cfg)] == ["ok"]


class TestStaleAndUsable:
    """The reimport surfaces' single source of truth (schema-bump migration).

    What ``stale_enabled`` must NOT report is as load-bearing as what it must:
    pitch is optional, so a source the user never configured, disabled, or
    deleted from disk can never gate a run.
    """

    def test_stale_enabled_reports_only_present_but_stale(self, root: Path) -> None:
        _make_source(root, "old", schema_version=2)
        _make_source(root, "current")
        cfg = replace(
            AnkiMinerConfig(),
            pitch_root=root,
            pitch_chain=(PitchSourceEntry("old"), PitchSourceEntry("current")),
        )
        registry = PitchSourceRegistry(root)
        registry.load()

        assert [m.source_id for m in registry.stale_enabled(cfg)] == ["old"]

    def test_stale_enabled_ignores_disabled_and_absent_entries(self, root: Path) -> None:
        _make_source(root, "off", schema_version=2)
        cfg = replace(
            AnkiMinerConfig(),
            pitch_root=root,
            pitch_chain=(
                PitchSourceEntry("off", enabled=False),
                PitchSourceEntry("deleted-from-disk"),
            ),
        )
        registry = PitchSourceRegistry(root)
        registry.load()

        assert registry.stale_enabled(cfg) == []

    def test_stale_enabled_empty_for_unconfigured_chain(self, root: Path) -> None:
        _make_source(root, "old", schema_version=2)
        cfg = replace(AnkiMinerConfig(), pitch_root=root)
        registry = PitchSourceRegistry(root)
        registry.load()

        assert registry.stale_enabled(cfg) == []

    def test_usable_enabled_requires_current_schema(self, root: Path) -> None:
        _make_source(root, "good")
        _make_source(root, "stale", schema_version=2)
        cfg = replace(
            AnkiMinerConfig(),
            pitch_root=root,
            pitch_chain=(
                PitchSourceEntry("good"),
                PitchSourceEntry("stale"),
                PitchSourceEntry("gone"),
            ),
        )
        registry = PitchSourceRegistry(root)
        registry.load()

        assert [m.source_id for m in registry.usable_enabled(cfg)] == ["good"]

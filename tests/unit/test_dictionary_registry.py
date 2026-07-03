"""Tests for DictionaryRegistry."""

import logging
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.providers.jisho_provider import JishoProvider
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)


def _seed_dict(root: Path, dict_id: str, source_name: str):
    folder = root / dict_id
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "index.sqlite"
    create_index(db)
    bulk_insert(db, [DictRow(term="x", reading=None, content="<div>x</div>", sequence=1)])
    write_meta(
        db,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": source_name,
            "format": "yomitan",
            "entry_count": "1",
        },
    )


class TestDictionaryRegistry:
    def test_scan_lists_installed_dicts(self, tmp_path: Path):
        _seed_dict(tmp_path, "daijirin-v1", "大辞林")
        _seed_dict(tmp_path, "jmdict-english", "JMdict (English)")

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        assert registry.get("daijirin-v1") is not None
        assert registry.get("jmdict-english") is not None

    def test_scan_skips_corrupt_folder_with_warning(self, tmp_path: Path, caplog):
        _seed_dict(tmp_path, "good", "Good")
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "index.sqlite").write_bytes(b"not a sqlite file")

        caplog.set_level(logging.WARNING)
        registry = DictionaryRegistry(tmp_path)
        registry.load()
        assert registry.get("good") is not None
        assert registry.get("bad") is None
        assert "corrupt" in caplog.text.lower() or "skipping" in caplog.text.lower()

    def test_build_chain_respects_config_order(self, tmp_path: Path):
        _seed_dict(tmp_path, "daijirin-v1", "大辞林")
        _seed_dict(tmp_path, "jmdict-english", "JMdict")

        config = AnkiMinerConfig()
        config = replace(
            config,
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="daijirin-v1", enabled=True),
                ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            ),
        )

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        chain = registry.build_provider_chain(config)

        assert len(chain) == 3
        assert isinstance(chain[0], IndexedDictProvider)
        assert chain[0].dict_id == "daijirin-v1"
        assert isinstance(chain[1], IndexedDictProvider)
        assert chain[1].dict_id == "jmdict-english"
        assert isinstance(chain[2], JishoProvider)

    def test_build_chain_skips_disabled_entries(self, tmp_path: Path):
        _seed_dict(tmp_path, "jmdict-english", "JMdict")
        config = AnkiMinerConfig()
        config = replace(
            config,
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            ),
        )

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        chain = registry.build_provider_chain(config)
        assert len(chain) == 1
        assert isinstance(chain[0], JishoProvider)

    def test_build_chain_drops_missing_dict_with_warning(self, tmp_path: Path, caplog):
        # No dicts on disk; config references one
        config = AnkiMinerConfig()
        config = replace(
            config,
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="ghost", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            ),
        )

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        caplog.set_level(logging.WARNING)
        chain = registry.build_provider_chain(config)
        assert len(chain) == 1
        assert isinstance(chain[0], JishoProvider)
        assert "ghost" in caplog.text or "not found" in caplog.text

    def test_disk_only_dict_discovered(self, tmp_path: Path):
        """Dictionaries on disk that aren't in the config should still be
        discovered so the UI can offer to enable them."""
        _seed_dict(tmp_path, "new-on-disk", "Surprise Dict")

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        assert registry.get("new-on-disk") is not None

    def test_dicts_root_is_file_returns_empty(self, tmp_path: Path):
        """If dicts_root points at a regular file, registry stays empty (no crash)."""
        bad_root = tmp_path / "not-a-dir"
        bad_root.write_text("oops")

        registry = DictionaryRegistry(bad_root)
        registry.load()
        assert registry.get("anything") is None

    def test_dicts_root_missing_returns_empty(self, tmp_path: Path):
        """If dicts_root doesn't exist, registry stays empty."""
        registry = DictionaryRegistry(tmp_path / "ghost")
        registry.load()
        assert registry.get("anything") is None

    def test_folder_without_index_sqlite_skipped(self, tmp_path: Path):
        """A child folder with no index.sqlite is silently skipped."""
        (tmp_path / "no-db").mkdir()
        _seed_dict(tmp_path, "real-dict", "Real")

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        assert registry.get("real-dict") is not None
        assert registry.get("no-db") is None

    def test_schema_mismatch_dict_excluded_from_chain(self, tmp_path: Path, caplog):
        """A dict on disk with the wrong schema_version must not appear in the chain."""
        import logging

        folder = tmp_path / "stale-dict"
        folder.mkdir(parents=True, exist_ok=True)
        db = folder / "index.sqlite"
        create_index(db)
        write_meta(
            db,
            {
                "schema_version": "999",  # wrong version
                "source_name": "Stale",
                "format": "yomitan",
                "entry_count": "0",
            },
        )

        config = AnkiMinerConfig()
        config = replace(
            config,
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="stale-dict", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            ),
        )

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        # list_dicts still shows it (with schema_ok=False)
        meta = registry.get("stale-dict")
        assert meta is not None
        assert meta.schema_ok is False

        # build_provider_chain excludes it with a warning
        caplog.set_level(logging.WARNING)
        chain = registry.build_provider_chain(config)
        assert len(chain) == 1
        assert isinstance(chain[0], JishoProvider)
        assert "stale-dict" in caplog.text or "schema" in caplog.text.lower()

    # ------------------------------------------------------------------
    # unlisted() tests
    # ------------------------------------------------------------------

    def test_unlisted_returns_disk_dict_not_in_chain(self, tmp_path: Path):
        """A dict on disk that has no matching chain entry is returned."""
        _seed_dict(tmp_path, "extra-dict", "Extra")
        config = AnkiMinerConfig()
        config = replace(config, dictionary_chain=())

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        result = registry.unlisted(config)

        assert len(result) == 1
        assert result[0].dict_id == "extra-dict"

    def test_unlisted_excludes_dict_already_in_chain(self, tmp_path: Path):
        """A dict referenced in the chain must not appear in unlisted()."""
        _seed_dict(tmp_path, "jmdict-english", "JMdict")
        config = AnkiMinerConfig()
        config = replace(
            config,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),),
        )

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        result = registry.unlisted(config)

        assert result == []

    def test_unlisted_excludes_schema_mismatched_dict(self, tmp_path: Path):
        """A dict with the wrong schema_version is excluded from unlisted()."""
        folder = tmp_path / "stale-dict"
        folder.mkdir(parents=True, exist_ok=True)
        db = folder / "index.sqlite"
        create_index(db)
        write_meta(
            db,
            {
                "schema_version": "999",
                "source_name": "Stale",
                "format": "yomitan",
                "entry_count": "0",
            },
        )
        config = AnkiMinerConfig()
        config = replace(config, dictionary_chain=())

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        result = registry.unlisted(config)

        assert result == []

    def test_unlisted_excludes_dict_in_disabled_chain_entry(self, tmp_path: Path):
        """A disabled chain entry still counts as listed (it has a re-enableable
        row), so its dict must not appear in unlisted()."""
        _seed_dict(tmp_path, "jmdict-english", "JMdict")
        config = AnkiMinerConfig()
        config = replace(
            config,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),),
        )

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        result = registry.unlisted(config)

        assert result == []

    def test_unlisted_returns_empty_when_all_dicts_in_chain(self, tmp_path: Path):
        """When every on-disk dict appears in the chain, unlisted() is empty."""
        _seed_dict(tmp_path, "dict-a", "Dict A")
        _seed_dict(tmp_path, "dict-b", "Dict B")
        config = AnkiMinerConfig()
        config = replace(
            config,
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="dict-a", enabled=True),
                ChainEntry(kind="indexed", dict_id="dict-b", enabled=True),
            ),
        )

        registry = DictionaryRegistry(tmp_path)
        registry.load()
        result = registry.unlisted(config)

        assert result == []


# ---------------------------------------------------------------------------
# OVH-048: registry.load() OSError guard
# ---------------------------------------------------------------------------


class TestDictionaryRegistryOSErrorGuard:
    """An OSError during dicts_root scan must yield an empty registry (no raise)."""

    def test_iterdir_oserror_yields_empty_registry(self, tmp_path: Path, caplog):
        """When iterdir() raises OSError, load() returns without raising and
        the registry stays empty."""
        _seed_dict(tmp_path, "good-dict", "Good Dict")

        caplog.set_level(logging.WARNING)
        registry = DictionaryRegistry(tmp_path)
        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            registry.load()

        # Registry must be empty — no entries loaded.
        assert registry.get("good-dict") is None

    def test_iterdir_oserror_logs_warning(self, tmp_path: Path, caplog):
        """An OSError during scan emits a warning containing the root path."""
        caplog.set_level(logging.WARNING)
        registry = DictionaryRegistry(tmp_path)
        with patch.object(Path, "iterdir", side_effect=OSError("stale NFS")):
            registry.load()

        assert str(tmp_path) in caplog.text

    def test_is_dir_oserror_yields_empty_registry(self, tmp_path: Path, caplog):
        """When is_dir() raises OSError, load() returns without raising."""
        caplog.set_level(logging.WARNING)
        registry = DictionaryRegistry(tmp_path)
        with patch.object(Path, "is_dir", side_effect=OSError("permission denied")):
            registry.load()

        assert registry.get("anything") is None


# ---------------------------------------------------------------------------
# 4.0: schema-staleness gate helpers
# ---------------------------------------------------------------------------


def _seed_stale_dict(root: Path, dict_id: str, source_name: str):
    """Seed an on-disk dict whose schema_version is wrong (needs reimport)."""
    folder = root / dict_id
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "index.sqlite"
    create_index(db)
    write_meta(
        db,
        {
            "schema_version": "1",  # stale (current is SCHEMA_VERSION)
            "source_name": source_name,
            "format": "yomitan",
            "entry_count": "0",
        },
    )


class TestStaleEnabled:
    def test_v2_slot_flags_schema_not_ok(self, tmp_path: Path):
        """A v2 dict (the pre-schema-v3 format) flags schema_ok=False."""
        folder = tmp_path / "v2-dict"
        folder.mkdir(parents=True, exist_ok=True)
        db = folder / "index.sqlite"
        create_index(db)
        write_meta(db, {"schema_version": "2", "source_name": "V2", "format": "yomitan", "entry_count": "0"})
        registry = DictionaryRegistry(tmp_path)
        registry.load()
        meta = registry.get("v2-dict")
        assert meta is not None and meta.schema_ok is False

    def test_enabled_stale_slot_flagged(self, tmp_path: Path):
        _seed_stale_dict(tmp_path, "old-dict", "Old Dict")
        config = replace(
            AnkiMinerConfig(),
            dicts_root=tmp_path,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="old-dict", enabled=True),),
        )
        registry = DictionaryRegistry(tmp_path)
        registry.load()
        stale = registry.stale_enabled(config)
        assert [m.dict_id for m in stale] == ["old-dict"]

    def test_current_slot_not_flagged(self, tmp_path: Path):
        _seed_dict(tmp_path, "cur-dict", "Current")
        config = replace(
            AnkiMinerConfig(),
            dicts_root=tmp_path,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="cur-dict", enabled=True),),
        )
        registry = DictionaryRegistry(tmp_path)
        registry.load()
        assert registry.stale_enabled(config) == []

    def test_disabled_stale_slot_not_flagged(self, tmp_path: Path):
        """A stale slot that is disabled in the chain proceeds (not gated)."""
        _seed_stale_dict(tmp_path, "old-dict", "Old Dict")
        config = replace(
            AnkiMinerConfig(),
            dicts_root=tmp_path,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="old-dict", enabled=False),),
        )
        registry = DictionaryRegistry(tmp_path)
        registry.load()
        assert registry.stale_enabled(config) == []

    def test_missing_slot_not_flagged(self, tmp_path: Path):
        """A referenced-but-absent dict is a different failure, not staleness."""
        config = replace(
            AnkiMinerConfig(),
            dicts_root=tmp_path,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="ghost", enabled=True),),
        )
        registry = DictionaryRegistry(tmp_path)
        registry.load()
        assert registry.stale_enabled(config) == []


class TestStaleHelpers:
    def test_stale_enabled_dicts_scans_and_flags(self, tmp_path: Path):
        from anki_miner.services.dictionary.registry import stale_enabled_dicts

        _seed_stale_dict(tmp_path, "old-dict", "Old Dict")
        config = replace(
            AnkiMinerConfig(),
            dicts_root=tmp_path,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="old-dict", enabled=True),),
        )
        assert [m.dict_id for m in stale_enabled_dicts(config)] == ["old-dict"]

    def test_format_message_single_and_plural(self, tmp_path: Path):
        from anki_miner.services.dictionary.registry import format_stale_reimport_message

        _seed_stale_dict(tmp_path, "a", "Alpha")
        _seed_stale_dict(tmp_path, "b", "Beta")
        registry = DictionaryRegistry(tmp_path)
        registry.load()
        one = format_stale_reimport_message([registry.get("a")])
        assert "Dictionary 'Alpha' needs reimport" in one
        assert "Settings → Dictionaries → Reimport All" in one
        two = format_stale_reimport_message([registry.get("a"), registry.get("b")])
        assert "Dictionaries 'Alpha', 'Beta' need reimport" in two

    def test_stale_dict_reimport_error(self, tmp_path: Path):
        from anki_miner.services.dictionary.registry import stale_dict_reimport_error

        _seed_stale_dict(tmp_path, "old-dict", "Old Dict")
        stale_cfg = replace(
            AnkiMinerConfig(),
            dicts_root=tmp_path,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="old-dict", enabled=True),),
        )
        assert stale_dict_reimport_error(stale_cfg) is not None
        clean_cfg = replace(stale_cfg, dictionary_chain=())
        assert stale_dict_reimport_error(clean_cfg) is None

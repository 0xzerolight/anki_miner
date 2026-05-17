"""Tests for DictionaryRegistry."""

import logging
from dataclasses import replace
from pathlib import Path

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

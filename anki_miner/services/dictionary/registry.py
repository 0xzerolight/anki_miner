"""Discovery + provider-chain assembly for installed dictionaries."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces.dictionary_provider import DictionaryProvider
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.storage import SCHEMA_VERSION, read_meta
from anki_miner.services.providers.jisho_provider import JishoProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DictMeta:
    dict_id: str
    source_name: str
    format: str
    entry_count: int
    schema_ok: bool
    db_path: Path


class DictionaryRegistry:
    """Scans the dictionaries folder and builds runtime provider chains."""

    def __init__(self, dicts_root: Path):
        self._root = dicts_root
        self._dicts: dict[str, DictMeta] = {}
        self._rescan()

    def _rescan(self) -> None:
        self._dicts.clear()
        if not self._root.is_dir():
            return
        for child in sorted(self._root.iterdir()):
            if not child.is_dir():
                continue
            db = child / "index.sqlite"
            if not db.exists():
                continue
            try:
                meta = read_meta(db)
            except sqlite3.DatabaseError as e:
                logger.warning("Skipping corrupt dictionary %s: %s", child.name, e)
                continue
            try:
                version = int(meta.get("schema_version", "0"))
            except ValueError:
                version = 0
            try:
                count = int(meta.get("entry_count", "0"))
            except ValueError:
                count = 0
            self._dicts[child.name] = DictMeta(
                dict_id=child.name,
                source_name=meta.get("source_name", child.name),
                format=meta.get("format", "unknown"),
                entry_count=count,
                schema_ok=(version == SCHEMA_VERSION),
                db_path=db,
            )

    def get(self, dict_id: str) -> DictMeta | None:
        return self._dicts.get(dict_id)

    def build_provider_chain(self, config: AnkiMinerConfig) -> list[DictionaryProvider]:
        """Build the ordered provider chain from config + disk state.

        Entries with enabled=False are skipped. Indexed entries whose dict_id
        is missing on disk are dropped with a warning. Jisho is included if
        its ChainEntry is enabled. Providers are returned in chain order.

        Caller is responsible for invoking provider.load() on each.
        """
        chain: list[DictionaryProvider] = []
        for entry in config.dictionary_chain:
            if not entry.enabled:
                continue
            if entry.kind == "indexed":
                if entry.dict_id is None:
                    logger.warning("Skipping indexed ChainEntry with null dict_id")
                    continue
                meta = self._dicts.get(entry.dict_id)
                if meta is None:
                    logger.warning(
                        "Dictionary '%s' referenced in config but not found in %s",
                        entry.dict_id,
                        self._root,
                    )
                    continue
                if not meta.schema_ok:
                    logger.warning(
                        "Dictionary '%s' has wrong schema_version; needs reimport",
                        entry.dict_id,
                    )
                    continue
                chain.append(
                    IndexedDictProvider(
                        dict_id=meta.dict_id,
                        db_path=meta.db_path,
                        display_name=meta.source_name,
                    )
                )
            elif entry.kind == "jisho":
                chain.append(JishoProvider(config.jisho_api_url, config.jisho_delay))
        return chain

"""Discovery + provider-chain assembly for installed dictionaries."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.interfaces.dictionary_provider import DictionaryProvider
from anki_miner.languages.registry import config_language, get_profile, language_display_name
from anki_miner.services._slot_registry import IndexedSlotRegistry, LoadResultSink
from anki_miner.services._sqlite_index import (
    is_generated_store_artifact,
    log_resource_inventory,
    meta_int,
    meta_language,
    read_ownership_marker,
    scan_index_root,
)
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.providers.jisho_provider import JishoProvider
from anki_miner.services.dictionary.storage import SCHEMA_VERSION
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DictMeta:
    dict_id: str
    source_name: str
    format: str
    entry_count: int
    schema_ok: bool
    db_path: Path
    #: Mining language this dictionary was imported for. Absent from every
    #: pre-transition meta.json, hence the tolerant "ja" default.
    language: str = "ja"


class DictionaryRegistry(IndexedSlotRegistry[DictMeta, ChainEntry]):
    """Scans the dictionaries folder and builds runtime provider chains."""

    _noun = "Dictionary"
    _logger = logger
    # Debug, not warning: this chain is rebuilt on every episode, and a missing
    # on-disk dict (e.g. the legacy 'jmdict-english' default slot a user never
    # migrated, or a transiently unreachable dicts_root) is skip-and-continue by
    # design. A genuinely empty chain is still surfaced at WARNING by
    # build_definition_service ("No offline dictionary — …").
    _missing_level = logging.DEBUG

    def load(self) -> None:
        self._slots = scan_index_root(
            self._root,
            self._parse_meta,
            child_prefilter=lambda child: (
                not is_generated_store_artifact(child.name)
                or read_ownership_marker(child) == ("dictionary", child.name)
            ),
            warn_label="dictionary",
        )
        log_resource_inventory(
            logger,
            "dictionary",
            self._root,
            sorted(self._slots),
            sorted(dict_id for dict_id, meta in self._slots.items() if not meta.schema_ok),
        )

    def _parse_meta(self, child: Path, db: Path, meta: dict[str, str]) -> DictMeta:
        source_name = meta.get("source_name")
        format_name = meta.get("format")
        version = meta_int(logger, child, meta, "schema_version")
        count = meta_int(logger, child, meta, "entry_count")
        # schema_ok policy: dictionaries require an exact-version match — a
        # mismatch is dropped from the chain and gated for reimport.
        return DictMeta(
            dict_id=child.name,
            source_name=source_name if isinstance(source_name, str) else child.name,
            format=format_name if isinstance(format_name, str) else "unknown",
            entry_count=count,
            schema_ok=(version == SCHEMA_VERSION),
            db_path=db,
            language=meta_language(meta),
        )

    def _chain(self, config: AnkiMinerConfig) -> Sequence[ChainEntry]:
        return config.dictionary_chain

    def _slot_id(self, entry: ChainEntry) -> str | None:
        return entry.dict_id if entry.kind == "indexed" else None

    def _meta_id(self, meta: DictMeta) -> str:
        return meta.dict_id

    def _skipped_language_notice(self, meta: DictMeta) -> str:
        return tr_format(
            QCoreApplication.translate("ResourceChain", "Dictionary '%1' is indexed for %2 and was skipped."),
            meta.source_name,
            language_display_name(meta.language),
        )

    def build_provider_chain(
        self,
        config: AnkiMinerConfig,
        *,
        load_result: LoadResultSink | None = None,
    ) -> list[DictionaryProvider]:
        """Build the ordered provider chain from config + disk state.

        Entries with enabled=False are skipped. Indexed entries whose dict_id
        is missing on disk are dropped with a warning. Indexed entries stamped
        for another mining language are dropped the same way. Jisho is included
        if its ChainEntry is enabled. Providers are returned in chain order.

        ``load_result`` is an optional sink for the user-facing warnings (duck
        typed: anything with a ``warnings`` list). ``None`` keeps them in the
        log only.

        Caller is responsible for invoking provider.load() on each.
        """
        language = config_language(config)
        chain: list[DictionaryProvider] = []
        for entry, meta in self._walk_enabled(config, language, load_result):
            if meta is not None:
                chain.append(
                    IndexedDictProvider(
                        dict_id=meta.dict_id,
                        db_path=meta.db_path,
                        display_name=meta.source_name,
                        keys=get_profile(language).dict_keys,
                    )
                )
            elif entry.kind == "indexed":
                logger.warning("Skipping indexed ChainEntry with null dict_id")
            elif entry.kind == "jisho":
                chain.append(JishoProvider(config.jisho_api_url, config.jisho_delay))
        return chain


def stale_enabled_dicts(config: AnkiMinerConfig) -> list[DictMeta]:
    """Build+scan a fresh registry and return enabled slots needing reimport.

    Convenience wrapper used by the startup migration prompt and the queue
    workers' pre-loop gate: it builds a :class:`DictionaryRegistry` from
    ``config.dicts_root``, loads it, and delegates to :meth:`stale_enabled`.
    ``load()`` swallows scan OSErrors internally, so this never raises for a
    missing / unreadable dicts folder (it simply reports no staleness).
    """
    registry = DictionaryRegistry(config.dicts_root)
    registry.load()
    return registry.stale_enabled(config)

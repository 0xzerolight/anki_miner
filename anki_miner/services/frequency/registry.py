"""Discovery + chain assembly for installed frequency sources.

Mirrors :class:`~anki_miner.services.dictionary.registry.DictionaryRegistry`:
scans ``<freqs_root>/<source_id>/index.sqlite`` folders, reads each source's
metadata (via the ``meta.json`` sidecar when fresh), and builds the ordered list
of :class:`IndexedFreqProvider` instances the additive aggregator consumes.

Unlike the dictionary chain there is no online fallback — every frequency source
is an on-disk indexed source. ``build_sources`` returns providers in config-chain
order, skipping disabled entries and any source missing / schema-mismatched on
disk; the caller invokes ``.load()`` on each (matching ``build_provider_chain``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig, FreqEntry
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
from anki_miner.services.frequency.providers.indexed_freq_provider import (
    IndexedFreqProvider,
)
from anki_miner.services.frequency.storage import SCHEMA_VERSION
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FreqSourceMeta:
    source_id: str
    source_name: str
    format: str
    entry_count: int
    # ``schema_ok`` = loadable/chain-includable. Only the current version is
    # accepted because schema bumps require reimporting canonicalized keys.
    # ``version`` is the raw on-disk schema version exposed for stale notices.
    schema_ok: bool
    version: int
    db_path: Path
    # True when the source is word-based (categorical): its rows carry level
    # labels stored display-only (storage.CATEGORICAL_RANK). Used only for the
    # settings-panel badge / import message — the runtime exclusion is driven by
    # the sentinel rank, not this flag, so build_sources ignores it.
    is_categorical: bool = False
    #: Mining language this source was imported for. Absent from every
    #: pre-transition meta.json, hence the tolerant "ja" default.
    language: str = "ja"


class FrequencySourceRegistry(IndexedSlotRegistry[FreqSourceMeta, FreqEntry]):
    """Scans the frequency-sources folder and builds runtime source lists."""

    _noun = "Frequency source"
    _logger = logger

    def load(self) -> None:
        self._slots = scan_index_root(
            self._root,
            self._parse_meta,
            child_prefilter=lambda child: (
                not is_generated_store_artifact(child.name) or read_ownership_marker(child) == ("frequency", child.name)
            ),
            warn_label="frequency source",
        )
        log_resource_inventory(
            logger,
            "frequency",
            self._root,
            sorted(self._slots),
            sorted(source_id for source_id, meta in self._slots.items() if not meta.schema_ok),
        )

    def _parse_meta(self, child: Path, db: Path, meta: dict[str, str]) -> FreqSourceMeta:
        source_name = meta.get("source_name")
        format_name = meta.get("format")
        version = meta_int(logger, child, meta, "schema_version")
        count = meta_int(logger, child, meta, "entry_count")
        return FreqSourceMeta(
            source_id=child.name,
            source_name=source_name if isinstance(source_name, str) else child.name,
            format=format_name if isinstance(format_name, str) else "unknown",
            entry_count=count,
            schema_ok=(version == SCHEMA_VERSION),
            version=version,
            db_path=db,
            # Explicit == "1" — meta values are strings, so bool("0") would be
            # truthy; only the literal "1" (or absent -> False) means categorical.
            is_categorical=(meta.get("is_categorical") == "1"),
            language=meta_language(meta),
        )

    def _chain(self, config: AnkiMinerConfig) -> Sequence[FreqEntry]:
        return config.frequency_chain

    def _slot_id(self, entry: FreqEntry) -> str | None:
        return entry.source_id

    def _meta_id(self, meta: FreqSourceMeta) -> str:
        return meta.source_id

    def _stale_detail(self, meta: FreqSourceMeta) -> str:
        return f"unsupported schema_version {meta.version}"

    def _skipped_language_notice(self, meta: FreqSourceMeta) -> str:
        return tr_format(
            QCoreApplication.translate("ResourceChain", "Frequency source '%1' is indexed for %2 and was skipped."),
            meta.source_name,
            language_display_name(meta.language),
        )

    def build_sources(
        self,
        config: AnkiMinerConfig,
        *,
        load_result: LoadResultSink | None = None,
    ) -> list[IndexedFreqProvider]:
        """Build the ordered provider list from config + disk state.

        Entries with enabled=False are skipped. Entries whose source_id is
        missing on disk, whose on-disk schema version is not current
        (``schema_ok=False``), or which are stamped for another mining
        language, are dropped with a warning. Providers are returned in chain
        order. This gate must match IndexedFreqProvider.load().

        ``load_result`` is an optional sink for the user-facing warnings (duck
        typed: anything with a ``warnings`` list). ``None`` keeps them in the
        log only.

        Caller is responsible for invoking provider.load() on each.
        """
        language = config_language(config)
        return [
            IndexedFreqProvider(
                source_id=meta.source_id,
                db_path=meta.db_path,
                display_name=meta.source_name,
                is_categorical=meta.is_categorical,
                keys=get_profile(language).dict_keys,
            )
            for _entry, meta in self._walk_enabled(config, language, load_result)
            if meta is not None
        ]


def stale_enabled_freq_sources(config: AnkiMinerConfig) -> list[FreqSourceMeta]:
    """Build+scan a fresh registry and return enabled slots needing reimport.

    Convenience wrapper for the startup migration prompt and the pre-run gate,
    matching ``stale_enabled_dicts``. ``load()`` swallows scan OSErrors, so this
    never raises for a missing / unreadable freqs folder — it reports no
    staleness instead.
    """
    registry = FrequencySourceRegistry(config.freqs_root)
    registry.load()
    return registry.stale_enabled(config)

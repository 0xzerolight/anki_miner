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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import config_language
from anki_miner.services._sqlite_index import (
    is_generated_store_artifact,
    meta_language,
    read_ownership_marker,
    scan_index_root,
)
from anki_miner.services.frequency.providers.indexed_freq_provider import (
    IndexedFreqProvider,
)
from anki_miner.services.frequency.storage import SCHEMA_VERSION
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps services import-free of gui
    from anki_miner.gui.utils.service_factory import ServiceLoadResult

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


class FrequencySourceRegistry:
    """Scans the frequency-sources folder and builds runtime source lists."""

    def __init__(self, freqs_root: Path):
        self._root = freqs_root
        self._sources: dict[str, FreqSourceMeta] = {}

    def load(self) -> None:
        self._sources = scan_index_root(
            self._root,
            self._parse_meta,
            child_prefilter=lambda child: (
                not is_generated_store_artifact(child.name) or read_ownership_marker(child) == ("frequency", child.name)
            ),
            warn_label="frequency source",
        )

    def _parse_meta(self, child: Path, db: Path, meta: dict[str, str]) -> FreqSourceMeta:
        source_name = meta.get("source_name")
        format_name = meta.get("format")
        raw_version = meta.get("schema_version")
        raw_count = meta.get("entry_count")
        try:
            version = int(raw_version) if isinstance(raw_version, str) else 0
        except (TypeError, ValueError):
            version = 0
        try:
            count = int(raw_count) if isinstance(raw_count, str) else 0
        except (TypeError, ValueError):
            count = 0
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

    def get(self, source_id: str) -> FreqSourceMeta | None:
        return self._sources.get(source_id)

    def unlisted(self, config: AnkiMinerConfig) -> list[FreqSourceMeta]:
        """Return on-disk sources not referenced by any chain entry.

        Only sources with schema_ok=True are returned. A stale or future source
        cannot be loaded and would be dropped by build_sources anyway. Results
        are sorted by source_id for deterministic ordering.

        A source referenced by a *disabled* chain entry is still considered
        listed (it has a visible, unchecked row the user can re-enable), so it
        is excluded — unlisted() surfaces only sources with no chain row at all.

        Does NOT call load(); callers control when the scan happens.
        """
        chained_ids: set[str] = {entry.source_id for entry in config.frequency_chain}
        return sorted(
            (meta for meta in self._sources.values() if meta.source_id not in chained_ids and meta.schema_ok),
            key=lambda m: m.source_id,
        )

    def stale_enabled(self, config: AnkiMinerConfig) -> list[FreqSourceMeta]:
        """Enabled chain slots present on disk but schema-mismatched.

        Mirrors ``DictionaryRegistry.stale_enabled``: the single source of truth
        for every reimport surface (settings row button, startup prompt, pre-run
        gate, health check). A slot missing on disk (``meta is None``) is NOT
        reported — the user may have deleted it deliberately, and there is no
        persisted ``source.<ext>`` left to rebuild from either way.

        Does NOT call load(); callers control when the scan happens.
        """
        stale: list[FreqSourceMeta] = []
        for entry in config.frequency_chain:
            if not entry.enabled or not entry.source_id:
                continue
            meta = self._sources.get(entry.source_id)
            if meta is not None and not meta.schema_ok:
                stale.append(meta)
        return sorted(stale, key=lambda m: m.source_id)

    def usable_enabled(self, config: AnkiMinerConfig) -> list[FreqSourceMeta]:
        """Enabled chain slots that can actually answer a lookup.

        Present on disk, schema-current, and holding at least one entry — read
        off this snapshot without opening a SQLite connection, so a readiness
        check can call it without file-locking an index Reimport All is about to
        replace (Windows).

        Does NOT call load(); callers control when the scan happens.
        """
        usable: list[FreqSourceMeta] = []
        for entry in config.frequency_chain:
            if not entry.enabled or not entry.source_id:
                continue
            meta = self._sources.get(entry.source_id)
            if meta is not None and meta.schema_ok and meta.entry_count > 0:
                usable.append(meta)
        return sorted(usable, key=lambda m: m.source_id)

    def build_sources(
        self,
        config: AnkiMinerConfig,
        *,
        load_result: ServiceLoadResult | None = None,
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
        sources: list[IndexedFreqProvider] = []
        for entry in config.frequency_chain:
            if not entry.enabled:
                continue
            meta = self._sources.get(entry.source_id)
            if meta is None:
                logger.warning(
                    "Frequency source '%s' referenced in config but not found in %s",
                    entry.source_id,
                    self._root,
                )
                continue
            if not meta.schema_ok:
                logger.warning(
                    "Frequency source '%s' has unsupported schema_version %s; needs reimport",
                    entry.source_id,
                    meta.version,
                )
                continue
            if meta.language != language:
                # A ko index answering a zh run returns confident nonsense;
                # skipping is the only safe read of a cross-language slot.
                logger.warning(
                    "Frequency source '%s' is indexed for '%s'; skipped for '%s'",
                    entry.source_id,
                    meta.language,
                    language,
                )
                if load_result is not None:
                    load_result.warnings.append(
                        tr_format(
                            QCoreApplication.translate("ResourceChain", "Frequency source '%1' is for %2; skipped"),
                            entry.source_id,
                            meta.language,
                        )
                    )
                continue
            sources.append(
                IndexedFreqProvider(
                    source_id=meta.source_id,
                    db_path=meta.db_path,
                    display_name=meta.source_name,
                    is_categorical=meta.is_categorical,
                )
            )
        return sources


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

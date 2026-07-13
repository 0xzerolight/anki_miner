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

from anki_miner.config import AnkiMinerConfig
from anki_miner.services._sqlite_index import scan_index_root
from anki_miner.services.frequency.providers.indexed_freq_provider import (
    IndexedFreqProvider,
)
from anki_miner.services.frequency.storage import SCHEMA_VERSION

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FreqSourceMeta:
    source_id: str
    source_name: str
    format: str
    entry_count: int
    # ``schema_ok`` = loadable/chain-includable (version in the supported range),
    # decoupled from is-latest: a v1 index reads fine (display_value treated as
    # absent), so schema_ok is True for it. ``version`` is the raw on-disk schema
    # version, exposed so an out-of-date notice can key on
    # ``version < SCHEMA_VERSION`` (a reimport gains display values but is never
    # required for correctness) — schema_ok no longer distinguishes v1 from v2.
    schema_ok: bool
    version: int
    db_path: Path
    # True when the source is word-based (categorical): its rows carry level
    # labels stored display-only (storage.CATEGORICAL_RANK). Used only for the
    # settings-panel badge / import message — the runtime exclusion is driven by
    # the sentinel rank, not this flag, so build_sources ignores it.
    is_categorical: bool = False


class FrequencySourceRegistry:
    """Scans the frequency-sources folder and builds runtime source lists."""

    def __init__(self, freqs_root: Path):
        self._root = freqs_root
        self._sources: dict[str, FreqSourceMeta] = {}

    def load(self) -> None:
        self._sources = scan_index_root(self._root, self._parse_meta, warn_label="frequency source")

    def _parse_meta(self, child: Path, db: Path, meta: dict[str, str]) -> FreqSourceMeta:
        try:
            version = int(meta.get("schema_version", "0"))
        except ValueError:
            version = 0
        try:
            count = int(meta.get("entry_count", "0"))
        except ValueError:
            count = 0
        return FreqSourceMeta(
            source_id=child.name,
            source_name=meta.get("source_name", child.name),
            format=meta.get("format", "unknown"),
            entry_count=count,
            # schema_ok policy: additive-only migrations, so every version from
            # 1..current is readable (older = fewer columns, filled as absent by
            # readers). A future version > SCHEMA_VERSION (written by a newer app)
            # is rejected — we don't know its schema.
            schema_ok=(1 <= version <= SCHEMA_VERSION),
            version=version,
            db_path=db,
            # Explicit == "1" — meta values are strings, so bool("0") would be
            # truthy; only the literal "1" (or absent -> False) means categorical.
            is_categorical=(meta.get("is_categorical") == "1"),
        )

    def get(self, source_id: str) -> FreqSourceMeta | None:
        return self._sources.get(source_id)

    def unlisted(self, config: AnkiMinerConfig) -> list[FreqSourceMeta]:
        """Return on-disk sources not referenced by any chain entry.

        Only sources with schema_ok=True are returned — an unsupported-version
        source (version outside 1..SCHEMA_VERSION) cannot be loaded and would be
        dropped by build_sources anyway. A readable-but-older v1 source has
        schema_ok=True, so it is offered normally. Results are sorted by
        source_id for deterministic ordering.

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

    def build_sources(self, config: AnkiMinerConfig) -> list[IndexedFreqProvider]:
        """Build the ordered provider list from config + disk state.

        Entries with enabled=False are skipped. Entries whose source_id is
        missing on disk, or whose on-disk schema version is outside the supported
        range (``schema_ok=False``), are dropped with a warning. A v1 index is
        supported and included (its display values simply read as absent).
        Providers are returned in chain order.

        This drop runs *before* IndexedFreqProvider.load(), so it must accept the
        same version range the provider does — else a v1 source would be dropped
        here and never reach the (backward-compatible) provider read.

        Caller is responsible for invoking provider.load() on each.
        """
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
            sources.append(
                IndexedFreqProvider(
                    source_id=meta.source_id,
                    db_path=meta.db_path,
                    display_name=meta.source_name,
                    is_categorical=meta.is_categorical,
                )
            )
        return sources

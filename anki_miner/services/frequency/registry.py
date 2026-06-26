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
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.frequency.providers.indexed_freq_provider import (
    IndexedFreqProvider,
)
from anki_miner.services.frequency.storage import SCHEMA_VERSION, read_meta_cached

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FreqSourceMeta:
    source_id: str
    source_name: str
    format: str
    entry_count: int
    schema_ok: bool
    db_path: Path


class FrequencySourceRegistry:
    """Scans the frequency-sources folder and builds runtime source lists."""

    def __init__(self, freqs_root: Path):
        self._root = freqs_root
        self._sources: dict[str, FreqSourceMeta] = {}

    def load(self) -> None:
        self._sources.clear()
        try:
            if not self._root.is_dir():
                return
            children = sorted(self._root.iterdir())
        except OSError as e:
            logger.warning(
                "Could not scan frequency-sources folder '%s': %s — no frequency sources will be loaded",
                self._root,
                e,
            )
            return
        for child in children:
            if not child.is_dir():
                continue
            db = child / "index.sqlite"
            if not db.exists():
                continue
            try:
                meta = read_meta_cached(db)
            except sqlite3.DatabaseError as e:
                logger.warning("Skipping corrupt frequency source %s: %s", child.name, e)
                continue
            try:
                version = int(meta.get("schema_version", "0"))
            except ValueError:
                version = 0
            try:
                count = int(meta.get("entry_count", "0"))
            except ValueError:
                count = 0
            self._sources[child.name] = FreqSourceMeta(
                source_id=child.name,
                source_name=meta.get("source_name", child.name),
                format=meta.get("format", "unknown"),
                entry_count=count,
                schema_ok=(version == SCHEMA_VERSION),
                db_path=db,
            )

    def get(self, source_id: str) -> FreqSourceMeta | None:
        return self._sources.get(source_id)

    def unlisted(self, config: AnkiMinerConfig) -> list[FreqSourceMeta]:
        """Return on-disk sources not referenced by any chain entry.

        Only sources with schema_ok=True are returned — schema-mismatched
        sources cannot be loaded and would be dropped by build_sources anyway.
        Results are sorted by source_id for deterministic ordering.

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
        missing on disk or schema-mismatched are dropped with a warning.
        Providers are returned in chain order.

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
                    "Frequency source '%s' has wrong schema_version; needs reimport",
                    entry.source_id,
                )
                continue
            sources.append(
                IndexedFreqProvider(
                    source_id=meta.source_id,
                    db_path=meta.db_path,
                    display_name=meta.source_name,
                )
            )
        return sources

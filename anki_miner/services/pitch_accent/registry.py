"""Discovery + chain assembly for installed pitch accent sources.

Mirrors :class:`~anki_miner.services.frequency.registry.FrequencySourceRegistry`:
scans ``<pitch_root>/<source_id>/index.sqlite`` folders, reads each source's
metadata (via the ``meta.json`` sidecar when fresh), and builds the ordered list
of :class:`IndexedPitchProvider` instances the first-hit-wins aggregator
consumes.

``build_sources`` returns providers in config-chain order, skipping disabled
entries and any source missing / schema-mismatched on disk; the caller invokes
``.load()`` on each (matching the frequency registry's contract).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.services._sqlite_index import (
    is_generated_store_artifact,
    read_ownership_marker,
    scan_index_root,
)
from anki_miner.services.pitch_accent.provider import IndexedPitchProvider
from anki_miner.services.pitch_accent.storage import SCHEMA_VERSION

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PitchSourceMeta:
    source_id: str
    source_name: str
    format: str
    entry_count: int
    # ``schema_ok`` = loadable/chain-includable. Pitch has a single schema
    # version so far, so this is simply ``version == SCHEMA_VERSION``; the raw
    # ``version`` is still exposed for a future out-of-date notice.
    schema_ok: bool
    version: int
    db_path: Path


class PitchSourceRegistry:
    """Scans the pitch-sources folder and builds runtime source lists."""

    def __init__(self, pitch_root: Path):
        self._root = pitch_root
        self._sources: dict[str, PitchSourceMeta] = {}

    def load(self) -> None:
        self._sources = scan_index_root(
            self._root,
            self._parse_meta,
            child_prefilter=lambda child: (
                not is_generated_store_artifact(child.name) or read_ownership_marker(child) == ("pitch", child.name)
            ),
            warn_label="pitch source",
        )

    def _parse_meta(self, child: Path, db: Path, meta: dict[str, str]) -> PitchSourceMeta:
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
        return PitchSourceMeta(
            source_id=child.name,
            source_name=source_name if isinstance(source_name, str) else child.name,
            format=format_name if isinstance(format_name, str) else "unknown",
            entry_count=count,
            schema_ok=(version == SCHEMA_VERSION),
            version=version,
            db_path=db,
        )

    def get(self, source_id: str) -> PitchSourceMeta | None:
        return self._sources.get(source_id)

    def unlisted(self, config: AnkiMinerConfig) -> list[PitchSourceMeta]:
        """Return on-disk sources not referenced by any chain entry.

        Only sources with schema_ok=True are returned — an unsupported-version
        source cannot be loaded and would be dropped by build_sources anyway.
        A source referenced by a *disabled* chain entry is still considered
        listed (it has a visible, unchecked row the user can re-enable), so it
        is excluded — unlisted() surfaces only sources with no chain row at all.
        Results are sorted by source_id for deterministic ordering.

        Does NOT call load(); callers control when the scan happens.
        """
        chained_ids: set[str] = {entry.source_id for entry in config.pitch_chain}
        return sorted(
            (meta for meta in self._sources.values() if meta.source_id not in chained_ids and meta.schema_ok),
            key=lambda m: m.source_id,
        )

    def build_sources(self, config: AnkiMinerConfig) -> list[IndexedPitchProvider]:
        """Build the ordered provider list from config + disk state.

        Entries with enabled=False are skipped. Entries whose source_id is
        missing on disk, or whose on-disk schema version is unsupported
        (``schema_ok=False``), are dropped with a warning. Providers are
        returned in chain order — the order IS the first-hit-wins priority.

        Caller is responsible for invoking provider.load() on each.
        """
        sources: list[IndexedPitchProvider] = []
        for entry in config.pitch_chain:
            if not entry.enabled:
                continue
            meta = self._sources.get(entry.source_id)
            if meta is None:
                logger.warning(
                    "Pitch source '%s' referenced in config but not found in %s",
                    entry.source_id,
                    self._root,
                )
                continue
            if not meta.schema_ok:
                logger.warning(
                    "Pitch source '%s' has unsupported schema_version %s; needs reimport",
                    entry.source_id,
                    meta.version,
                )
                continue
            sources.append(
                IndexedPitchProvider(
                    source_id=meta.source_id,
                    db_path=meta.db_path,
                    display_name=meta.source_name,
                )
            )
        return sources

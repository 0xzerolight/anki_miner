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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig, PitchSourceEntry
from anki_miner.languages.registry import config_language, language_display_name
from anki_miner.services._slot_registry import IndexedSlotRegistry
from anki_miner.services._sqlite_index import (
    is_generated_store_artifact,
    log_resource_inventory,
    meta_int,
    meta_language,
    read_ownership_marker,
    scan_index_root,
)
from anki_miner.services.pitch_accent.provider import IndexedPitchProvider
from anki_miner.services.pitch_accent.storage import SCHEMA_VERSION
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps services import-free of gui
    from anki_miner.gui.utils.service_factory import ServiceLoadResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PitchSourceMeta:
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
    #: Mining language this source was imported for. Absent from every
    #: pre-transition meta.json, hence the tolerant "ja" default.
    language: str = "ja"


class PitchSourceRegistry(IndexedSlotRegistry[PitchSourceMeta, PitchSourceEntry]):
    """Scans the pitch-sources folder and builds runtime source lists."""

    def load(self) -> None:
        self._slots = scan_index_root(
            self._root,
            self._parse_meta,
            child_prefilter=lambda child: (
                not is_generated_store_artifact(child.name) or read_ownership_marker(child) == ("pitch", child.name)
            ),
            warn_label="pitch source",
        )
        log_resource_inventory(
            logger,
            "pitch",
            self._root,
            sorted(self._slots),
            sorted(source_id for source_id, meta in self._slots.items() if not meta.schema_ok),
        )

    def _parse_meta(self, child: Path, db: Path, meta: dict[str, str]) -> PitchSourceMeta:
        source_name = meta.get("source_name")
        format_name = meta.get("format")
        version = meta_int(logger, child, meta, "schema_version")
        count = meta_int(logger, child, meta, "entry_count")
        return PitchSourceMeta(
            source_id=child.name,
            source_name=source_name if isinstance(source_name, str) else child.name,
            format=format_name if isinstance(format_name, str) else "unknown",
            entry_count=count,
            schema_ok=(version == SCHEMA_VERSION),
            version=version,
            db_path=db,
            language=meta_language(meta),
        )

    def _chain(self, config: AnkiMinerConfig) -> Sequence[PitchSourceEntry]:
        return config.pitch_chain

    def _slot_id(self, entry: PitchSourceEntry) -> str | None:
        return entry.source_id

    def _meta_id(self, meta: PitchSourceMeta) -> str:
        return meta.source_id

    def build_sources(
        self,
        config: AnkiMinerConfig,
        *,
        load_result: ServiceLoadResult | None = None,
    ) -> list[IndexedPitchProvider]:
        """Build the ordered provider list from config + disk state.

        Entries with enabled=False are skipped. Entries whose source_id is
        missing on disk, whose on-disk schema version is unsupported
        (``schema_ok=False``), or which are stamped for another mining
        language, are dropped with a warning. Providers are returned in chain
        order — the order IS the first-hit-wins priority.

        ``load_result`` is an optional sink for the user-facing warnings (duck
        typed: anything with a ``warnings`` list). ``None`` keeps them in the
        log only.

        Caller is responsible for invoking provider.load() on each.
        """
        language = config_language(config)
        sources: list[IndexedPitchProvider] = []
        for entry in config.pitch_chain:
            if not entry.enabled:
                continue
            meta = self._slots.get(entry.source_id)
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
            if meta.language != language:
                # A ko index answering a zh run returns confident nonsense;
                # skipping is the only safe read of a cross-language slot.
                logger.warning(
                    "Pitch source '%s' is indexed for '%s'; skipped for '%s'",
                    entry.source_id,
                    meta.language,
                    language,
                )
                if load_result is not None:
                    load_result.warnings.append(
                        tr_format(
                            QCoreApplication.translate(
                                "ResourceChain", "Pitch source '%1' is indexed for %2 and was skipped."
                            ),
                            meta.source_name,
                            language_display_name(meta.language),
                        )
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


def stale_enabled_pitch_sources(config: AnkiMinerConfig) -> list[PitchSourceMeta]:
    """Build+scan a fresh registry and return enabled slots needing reimport.

    Convenience wrapper for the startup migration prompt and the pre-run gate,
    matching ``stale_enabled_dicts``. ``load()`` swallows scan OSErrors, so this
    never raises for a missing / unreadable pitch folder — it reports no
    staleness instead.
    """
    registry = PitchSourceRegistry(config.pitch_root)
    registry.load()
    return registry.stale_enabled(config)

"""Discovery + fetcher-chain assembly for installed audio packs."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.services._sqlite_index import (
    is_generated_store_artifact,
    read_ownership_marker,
    scan_index_root,
)
from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher
from anki_miner.services.audio_packs.storage import SCHEMA_VERSION

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioPackMeta:
    """Registry entry for a discovered audio pack."""

    pack_id: str
    source: str
    format: str
    entry_count: int
    schema_ok: bool
    pack_dir: Path
    pack_dir_exists: bool
    db_path: Path
    source_db: Path | None = None

    @property
    def source_available(self) -> bool:
        """Whether the audio this pack serves is actually reachable.

        A folder pack needs its ``pack_dir``; an ``android_db`` pack needs the
        external database it was registered against. ``pack_dir_exists`` stays
        literal so it does not have to answer both questions.
        """
        if self.format == "android_db":
            return self.source_db is not None and self.source_db.is_file()
        return self.pack_dir_exists


class AudioPackRegistry:
    """Scans the audio_packs folder and builds runtime fetcher chains.

    Mirrors :class:`~anki_miner.services.dictionary.registry.DictionaryRegistry`:
    ``__init__`` is I/O-free; all disk access happens inside ``load()``.
    """

    def __init__(self, packs_root: Path) -> None:
        self._root = packs_root
        self._packs: dict[str, AudioPackMeta] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Scan *packs_root* for installed audio packs.

        Each subdirectory that is not hidden (does not start with ``.``) and
        contains an ``index.sqlite`` is considered a candidate.  Hidden
        directories (covers ``.staging-*`` importer staging) and names
        containing ``.bak-`` (importer overwrite backups like
        ``<pack>.bak-<timestamp>``) are explicitly skipped.  Packs with
        unreadable/corrupt meta are skipped with a warning. Schema-mismatched
        packs are retained with ``schema_ok=False`` so settings can offer
        repair, but runtime chain assembly excludes them.
        """
        # Audio widens the meta-read guard to (sqlite3.Error, OSError) and
        # pre-filters staging/backup dirs before the meta read (both preserved
        # via scan_index_root's params).
        self._packs = scan_index_root(
            self._root,
            self._parse_meta,
            child_prefilter=self._is_candidate,
            exception_types=(sqlite3.Error, OSError),
            warn_label="audio pack",
        )

    @staticmethod
    def _is_candidate(child: Path) -> bool:
        # Skip hidden dirs (importer staging artefacts) and importer overwrite
        # backups (<pack>.bak-<timestamp> siblings): a failed Windows rmtree must
        # not surface a stale staging dir or backup as a pack.
        return not is_generated_store_artifact(child.name) or read_ownership_marker(child) == ("audio", child.name)

    def _parse_meta(self, child: Path, db: Path, meta: dict[str, str]) -> AudioPackMeta:
        # Schema version check — mismatch means the pack needs re-import.
        try:
            version = int(meta.get("schema_version", "0"))
        except ValueError:
            version = 0
        if version != SCHEMA_VERSION:
            logger.warning(
                "Audio pack '%s' has schema_version=%s, expected %s — needs re-import",
                child.name,
                version,
                SCHEMA_VERSION,
            )

        try:
            count = int(meta.get("entry_count", "0"))
        except ValueError:
            count = 0

        pack_dir_str = meta.get("pack_dir", "")
        pack_dir = Path(pack_dir_str) if pack_dir_str else child
        source_db_str = meta.get("source_db", "")
        source_db = Path(source_db_str) if source_db_str else None

        return AudioPackMeta(
            pack_id=meta.get("pack_id", child.name),
            source=meta.get("source", child.name),
            format=meta.get("format", "unknown"),
            entry_count=count,
            schema_ok=(version == SCHEMA_VERSION),
            pack_dir=pack_dir,
            pack_dir_exists=pack_dir.is_dir(),
            db_path=db,
            source_db=source_db,
        )

    @property
    def packs(self) -> dict[str, AudioPackMeta]:
        """Snapshot of loaded packs keyed by folder name (pack_id)."""
        return dict(self._packs)

    def unlisted(self, config: AnkiMinerConfig) -> list[AudioPackMeta]:
        """Return schema-valid on-disk packs absent from the audio chain."""
        chained_ids = {
            entry.pack_id
            for entry in config.expression_audio_chain
            if entry.kind == "pack" and entry.pack_id is not None
        }
        return sorted(
            (meta for meta in self._packs.values() if meta.pack_id not in chained_ids and meta.schema_ok),
            key=lambda meta: meta.pack_id,
        )

    # ------------------------------------------------------------------
    # Chain assembly
    # ------------------------------------------------------------------

    def build_fetcher_chain(
        self,
        config: AnkiMinerConfig,
        cache_dir: Path,
    ) -> list[LocalAudioPackFetcher]:
        """Build an ordered list of pack fetchers from config + disk state.

        Design mirrors ``DictionaryRegistry.build_provider_chain``:
        * Disabled entries are skipped silently.
        * ``kind="pack"`` entries whose pack_id is unknown on disk are skipped
          with a warning (pack was removed since config was written).
        * Packs with a stale index schema are skipped with a warning.
        * Packs whose ``pack_dir`` is missing on disk are skipped with a
          warning (audio files moved or external drive unplugged).
        * Non-pack entries (``kind="jpod101"``, ``kind="googletts"``) are
          silently skipped here; they are composed by the service factory (T7)
          around the list this method returns.  Unlike
          ``DictionaryRegistry.build_provider_chain``, which
          builds ``JishoProvider`` inline, this registry intentionally returns
          only local pack fetchers and carries no network-fetcher knowledge.

        Returns only :class:`LocalAudioPackFetcher` instances (pack entries).
        """
        chain: list[LocalAudioPackFetcher] = []
        for entry in config.expression_audio_chain:
            if not entry.enabled:
                continue
            if entry.kind != "pack":
                # jpod101 (and any future network kind) composed by the factory.
                continue
            if entry.pack_id is None:
                logger.warning("Skipping audio pack ChainEntry with null pack_id")
                continue
            meta = self._packs.get(entry.pack_id)
            if meta is None:
                logger.warning(
                    "Audio pack '%s' referenced in config but not found in %s",
                    entry.pack_id,
                    self._root,
                )
                continue
            # A stale index must never reach the runtime fetcher chain.
            if not meta.schema_ok:
                logger.warning(
                    "Audio pack '%s' has wrong schema_version; needs reimport",
                    entry.pack_id,
                )
                continue
            if not meta.source_available:
                logger.warning(
                    "Audio pack '%s' source missing (%s); skipping — moved or deleted?",
                    entry.pack_id,
                    meta.source_db if meta.format == "android_db" else meta.pack_dir,
                )
                continue
            chain.append(
                LocalAudioPackFetcher(
                    db_path=meta.db_path,
                    pack_dir=meta.pack_dir,
                    pack_id=meta.pack_id,
                    cache_dir=cache_dir,
                    blob_db_path=meta.source_db if meta.format == "android_db" else None,
                )
            )
        return chain

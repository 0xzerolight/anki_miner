"""Discovery + fetcher-chain assembly for installed audio packs."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.services._sqlite_index import scan_index_root
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
    pack_dir: Path
    pack_dir_exists: bool
    db_path: Path


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
        unreadable/corrupt meta or a schema_version mismatch are skipped
        with a warning.
        """
        # Audio widens the meta-read guard to (sqlite3.Error, OSError) and
        # pre-filters staging/backup dirs before the meta read (both preserved
        # via scan_index_root's params); schema-mismatch drop-at-scan stays in
        # _parse_meta (returns None to skip, unlike dict/freq's schema_ok flag).
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
        return not child.name.startswith(".") and ".bak-" not in child.name

    def _parse_meta(self, child: Path, db: Path, meta: dict[str, str]) -> AudioPackMeta | None:
        # Schema version check — mismatch means the pack needs re-import.
        try:
            version = int(meta.get("schema_version", "0"))
        except ValueError:
            version = 0
        if version != SCHEMA_VERSION:
            logger.warning(
                "Audio pack '%s' has schema_version=%s, expected %s — needs re-import; skipping",
                child.name,
                version,
                SCHEMA_VERSION,
            )
            return None

        try:
            count = int(meta.get("entry_count", "0"))
        except ValueError:
            count = 0

        pack_dir_str = meta.get("pack_dir", "")
        pack_dir = Path(pack_dir_str) if pack_dir_str else child

        return AudioPackMeta(
            pack_id=meta.get("pack_id", child.name),
            source=meta.get("source", child.name),
            format=meta.get("format", "unknown"),
            entry_count=count,
            pack_dir=pack_dir,
            pack_dir_exists=pack_dir.is_dir(),
            db_path=db,
        )

    @property
    def packs(self) -> dict[str, AudioPackMeta]:
        """Snapshot of loaded packs keyed by folder name (pack_id)."""
        return dict(self._packs)

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
            if not meta.pack_dir_exists:
                logger.warning(
                    "Audio pack '%s' pack_dir missing (%s); skipping — audio files moved?",
                    entry.pack_id,
                    meta.pack_dir,
                )
                continue
            chain.append(
                LocalAudioPackFetcher(
                    db_path=meta.db_path,
                    pack_dir=meta.pack_dir,
                    pack_id=meta.pack_id,
                    cache_dir=cache_dir,
                )
            )
        return chain

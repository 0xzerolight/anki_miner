"""Discovery + fetcher-chain assembly for installed audio packs."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher
from anki_miner.services.audio_packs.storage import SCHEMA_VERSION, read_meta_cached

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
        self._packs.clear()
        if not self._root.is_dir():
            return
        for child in sorted(self._root.iterdir()):
            if not child.is_dir():
                continue
            # Skip hidden dirs — importer staging artefacts.
            if child.name.startswith("."):
                continue
            # Skip importer overwrite backups (<pack>.bak-<timestamp> siblings):
            # a failed Windows rmtree must not surface a stale backup as a pack.
            if ".bak-" in child.name:
                continue
            db = child / "index.sqlite"
            if not db.exists():
                continue
            try:
                meta = read_meta_cached(db)
            except (sqlite3.Error, OSError) as exc:
                logger.warning("Skipping corrupt audio pack %s: %s", child.name, exc)
                continue

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
                continue

            try:
                count = int(meta.get("entry_count", "0"))
            except ValueError:
                count = 0

            pack_dir_str = meta.get("pack_dir", "")
            pack_dir = Path(pack_dir_str) if pack_dir_str else child

            self._packs[child.name] = AudioPackMeta(
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
        * ``kind="jpod101"`` entries are silently skipped here; they are
          composed by the service factory (T7) around the list this method
          returns.  Unlike ``DictionaryRegistry.build_provider_chain``, which
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

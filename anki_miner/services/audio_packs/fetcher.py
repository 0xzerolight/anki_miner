"""Local audio pack fetcher — ExpressionAudioFetcher implementation."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

from anki_miner.services.audio_fetch_common import (
    find_cached_by_stem as _find_cached_by_stem,
)
from anki_miner.services.audio_fetch_common import (
    first_candidate_hit as _first_candidate_hit,
)
from anki_miner.services.audio_fetch_common import (
    record_cached_path as _record_cached_path,
)
from anki_miner.services.audio_packs import storage
from anki_miner.utils.file_utils import safe_filename
from anki_miner.utils.text_utils import hiragana_to_katakana, is_kana_only, katakana_to_hiragana

logger = logging.getLogger(__name__)


class LocalAudioPackFetcher:
    """Fetches word pronunciation audio from a locally indexed audio pack.

    Conforms to the :class:`~anki_miner.interfaces.ExpressionAudioFetcher`
    Protocol structurally (never raises; returns Path or None).

    Cache strategy: successful hits are copied into *cache_dir* under a
    pack-prefixed name so Anki media filenames remain globally unique.
    Misses are NOT cached (no .miss markers) because local SQLite lookups are
    cheap — re-querying on every call avoids stale negatives after re-import.

    Connection idiom: a new read-only connection is opened per ``fetch`` call
    and closed in a finally block.  This avoids long-lived handles that would
    block pack removal or re-import on Windows (where open file handles
    prevent directory deletion).
    """

    def __init__(
        self,
        db_path: Path,
        pack_dir: Path,
        pack_id: str,
        cache_dir: Path,
    ) -> None:
        self._db_path = db_path
        self._pack_dir = pack_dir.resolve()
        self._pack_id = pack_id
        self._cache_dir = cache_dir

    @property
    def pack_id(self) -> str:
        """Identifier for this audio pack (read-only).

        Used by the service factory to align registry fetchers with
        config chain entries when composing the final audio chain.
        """
        return self._pack_id

    # ------------------------------------------------------------------
    # ExpressionAudioFetcher Protocol
    # ------------------------------------------------------------------

    def fetch(
        self,
        mined_form: str,
        reading: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Return a cached copy of the best matching audio file, or None.

        Args:
            mined_form: Word as mined onto the card (kanji/surface form).
            reading: Kana reading of the word — usually. When the tokenizer has
                no kana for a word (OOV), it falls back to the kanji surface,
                so this can arrive non-kana; and a direct caller may pass ""
                (unreachable from the mining ladder, which drops empty pairs —
                see ``orchestration.audio_stage._expression_audio_candidates``).
                A pure-kana reading takes the exact-match path (with a
                katakana-folded retry: packs store ``kana`` verbatim, the miner
                folds to hiragana). Anything else takes the wildcard path,
                served ONLY when the pack's rows for the expression are
                unambiguous — ≤1 distinct hiragana-folded reading — else only
                NULL-reading (wildcard) rows are eligible. That guard keeps the
                original homograph safety: 辛い (からい vs つらい) never serves
                or caches a guessed pronunciation under the word's key.
            cancelled_check: Optional zero-argument callable that returns True
                when the caller has requested cancellation.  Consulted once at
                entry (before the sqlite open) — local lookups are fast enough
                that no further checkpoints are needed.  Never raises.

        Returns:
            Path to a cached audio file, or None if unavailable. Never raises.
        """
        if not mined_form.strip():
            return None
        reading = reading.strip()

        if cancelled_check is not None and cancelled_check():
            return None

        # 1. Cache hit: shared index matches any extension and skips leftover
        #    .part staging files (e.g. stem.mp3.part from a crashed prior copy).
        stem = safe_filename(f"{self._pack_id}_{mined_form}_{reading}")
        existing = _find_cached_by_stem(self._cache_dir, stem)
        if existing is not None:
            return existing

        # 2. Query the SQLite index.
        conn: sqlite3.Connection | None = None
        try:
            try:
                conn = storage.open_readonly(self._db_path)
            except (sqlite3.Error, OSError) as exc:
                logger.debug("LocalAudioPackFetcher: cannot open %s: %s", self._db_path, exc)
                return None

            try:
                if is_kana_only(reading):
                    rows = storage.lookup(conn, mined_form, reading)
                    katakana_variant = hiragana_to_katakana(reading)
                    if not rows and katakana_variant != reading:
                        # Packs store kana verbatim (often katakana for
                        # NHK/SMK) while miner readings are hiragana-folded;
                        # retry the exact match in the other script.
                        rows = storage.lookup(conn, mined_form, katakana_variant)
                else:
                    # Non-kana (or empty) reading: the exact key is useless.
                    # Wildcard the expression, then guard on ambiguity.
                    rows = storage.lookup(conn, mined_form, "")
                    distinct = {katakana_to_hiragana(r.reading) for r in rows if r.reading}
                    if len(distinct) > 1:
                        # Genuinely ambiguous — only wildcard (NULL-reading)
                        # rows may serve, matching what the old exact path
                        # returned for a non-kana reading.
                        rows = [r for r in rows if r.reading is None]
            except (sqlite3.Error, OSError) as exc:
                logger.debug("LocalAudioPackFetcher: lookup failed for %r: %s", mined_form, exc)
                return None
        finally:
            if conn is not None:
                conn.close()

        # 3. Walk rows in id order; apply containment guard; copy first safe hit.
        for row in rows:
            candidate = self._resolve_safe(row.file)
            if candidate is None:
                continue

            # 4. Copy winning file into cache atomically.
            orig_suffix = candidate.suffix
            cache_path = self._cache_dir / f"{stem}{orig_suffix}"
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                part_path = cache_path.with_suffix(orig_suffix + ".part")
                shutil.copy2(candidate, part_path)
                os.replace(part_path, cache_path)
                _record_cached_path(self._cache_dir, cache_path)
            except OSError as exc:
                logger.debug("LocalAudioPackFetcher: copy failed for %s: %s", candidate, exc)
                return None

            # Never return the in-place pack path — Anki storeMediaFile uses
            # path.name verbatim and would silently overwrite other packs'
            # files if names collide.
            return cache_path

        return None

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Try each candidate form, returning the first pack hit."""
        return _first_candidate_hit(self, candidates, cancelled_check)

    def close(self) -> None:
        """No-op: sqlite connections are opened and closed per ``fetch`` call.

        This fetcher holds no long-lived sqlite handle (see the connection
        idiom in the class docstring), so there is nothing to release between
        sequential mining runs. Present for uniform duck-typed ``close()``
        fan-out from ChainedExpressionAudioFetcher.
        """
        # Nothing to close — each fetch opens its own short-lived connection.
        pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_safe(self, rel_file: str) -> Path | None:
        """Resolve *rel_file* relative to pack_dir with a containment guard.

        Mirrors ``_resolve_dict_media_path`` in the dictionary layer: the
        resolved path must start with pack_dir (after resolve()) to prevent
        path-traversal attacks (e.g. ``../../evil.mp3`` in a malicious pack).

        Returns None when the file is outside pack_dir, missing, or the path
        cannot be resolved.
        """
        try:
            resolved = (self._pack_dir / rel_file).resolve()
        except (OSError, ValueError):
            return None

        # Containment check: resolved must be inside pack_dir.
        try:
            resolved.relative_to(self._pack_dir)
        except ValueError:
            logger.warning(
                "LocalAudioPackFetcher: traversal attempt blocked: %r in pack %r",
                rel_file,
                self._pack_id,
            )
            return None

        # is_file() does not suppress EACCES: a PermissionError here (e.g. an
        # unreadable dir on the resolved path) would propagate out of fetch and
        # abort the never-raises mining loop.
        try:
            if not resolved.is_file():
                return None
        except OSError:
            return None

        return resolved

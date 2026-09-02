"""Local audio pack fetcher — ExpressionAudioFetcher implementation."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Callable
from functools import partial
from pathlib import Path

from anki_miner.services.audio_fetch_common import MAX_AUDIO_BYTES
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
from anki_miner.utils.robust_fs import robust_rmtree
from anki_miner.utils.text_utils import hiragana_to_katakana, is_kana_only, katakana_to_hiragana

logger = logging.getLogger(__name__)


def _pack_cache_dir(cache_dir: Path, pack_id: str) -> Path:
    """Return the cache directory owned exclusively by ``pack_id``."""
    owner_key = hashlib.sha256(pack_id.encode("utf-8")).hexdigest()
    return cache_dir / owner_key


def purge_pack_cache(cache_dir: Path, pack_id: str) -> int:
    """Delete positive cache entries owned by ``pack_id``."""
    owned_dir = _pack_cache_dir(cache_dir, pack_id)
    invalidated = owned_dir.with_name(f".{owned_dir.name}.purge-{uuid.uuid4().hex}")
    try:
        os.replace(owned_dir, invalidated)
    except FileNotFoundError:
        return 0
    try:
        removed = sum(1 for path in invalidated.iterdir() if not path.name.endswith(".part") and path.is_file())
    except OSError:
        removed = 0
    robust_rmtree(invalidated, mode="outcome")
    return removed


class LocalAudioPackFetcher:
    """Fetches word pronunciation audio from a locally indexed audio pack.

    Conforms to the :class:`~anki_miner.interfaces.ExpressionAudioFetcher`
    Protocol structurally (never raises; returns Path or None).

    Cache strategy: successful hits are copied into a pack-owned subdirectory
    of *cache_dir*, under a pack-prefixed name so Anki media filenames remain
    globally unique.
    Misses are NOT cached (no .miss markers) because local SQLite lookups are
    cheap — re-querying on every call avoids stale negatives after re-import.

    Connection idiom: the read-only sqlite handle is opened lazily on the
    first ``fetch`` call and held open across every later call on this
    instance. An android_db pack's lookup index and audio blobs are the SAME
    multi-GB file (``blob_db_path``), so reopening per call cost up to two
    fresh opens of that file per word. ``close()`` releases the handle —
    callers must close it before removing or re-importing a pack (Windows
    keeps a directory locked while a file inside it is open); a ``fetch``
    after ``close`` reopens lazily.
    """

    def __init__(
        self,
        db_path: Path,
        pack_dir: Path,
        pack_id: str,
        cache_dir: Path,
        blob_db_path: Path | None = None,
    ) -> None:
        self._db_path = db_path
        self._pack_dir = pack_dir.resolve()
        self._pack_id = pack_id
        self._cache_dir = _pack_cache_dir(cache_dir, pack_id)
        self._blob_db_path = blob_db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def pack_id(self) -> str:
        """Identifier for this audio pack (read-only).

        Used by the service factory to align registry fetchers with
        config chain entries when composing the final audio chain.
        """
        return self._pack_id

    @property
    def pack_dir(self) -> Path:
        """Resolved source folder of this pack (read-only).

        Named on the chain's budget-expiry log line: a pack that blows the
        per-word budget is one whose folder sits on a slow medium, and the
        folder is what the user has to move.
        """
        return self._pack_dir

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

        # 2. Query the SQLite index, resolve the winning row, and cache it — all
        #    inside one guarded scope so `rows` is only ever read where it was
        #    just assigned: a failure anywhere (the connection, the lookup, the
        #    kana-script helpers below, or row resolution) returns None instead
        #    of falling through to code that reads `rows` past a point where it
        #    might never have been bound.
        try:
            # An android_db pack's managed index holds no rows: it is a
            # metadata token, and the entries live in the registered source db.
            conn = self._connection()

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

            # 3a. An android_db pack keeps its audio as blobs in the source db:
            #     there is no file to resolve, so the containment guard is moot.
            if self._blob_db_path is not None:
                return self._serve_from_blobs(conn, rows, stem)

            # 3. Walk rows in id order; apply containment guard; copy first safe hit.
            for row in rows:
                candidate = self._resolve_safe(row.file)
                if candidate is None:
                    continue

                # 4. Copy winning file into cache atomically. Never return the
                #    in-place pack path — Anki storeMediaFile uses path.name
                #    verbatim and would silently overwrite other packs' files if
                #    names collide.
                return self._write_cached(stem, candidate.suffix, partial(shutil.copy2, candidate))

            return None
        # Broad Exception is intentional and correct: sqlite3.Error/OSError
        # cover the connection and query, but the kana-script helpers above
        # (is_kana_only / hiragana_to_katakana / katakana_to_hiragana) can
        # raise on pathological input too, and fetch() has no caller-side
        # try/except — this method must own every failure mode.
        except Exception as exc:  # noqa: BLE001 — never raise per the fetcher protocol contract
            logger.debug("LocalAudioPackFetcher: fetch failed for %r (pack=%s): %s", mined_form, self._pack_id, exc)
            return None

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Try each candidate form, returning the first pack hit."""
        return _first_candidate_hit(self, candidates, cancelled_check)

    def close(self) -> None:
        """Release the persistent sqlite handle, if one was ever opened.

        Drops the read-only connection held since the first ``fetch`` call so
        Settings → Remove/re-import can ``rmtree`` the pack out from under an
        idle fetcher (Windows keeps a directory locked while a file inside it
        stays open). Safe to call on a fetcher that never fetched, and safe to
        call more than once; a later ``fetch`` reopens lazily.
        """
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        """Return the persistent read-only connection, opening it lazily.

        An android_db pack's lookup index and audio blobs are the SAME
        multi-GB file (``blob_db_path``); sharing one handle between the
        entry lookup and the blob walk (see ``_serve_from_blobs``) collapses
        what used to be up to two opens of that file per ``fetch`` call into
        at most one per fetcher lifetime.
        """
        if self._conn is None:
            lookup_db = self._blob_db_path or self._db_path
            self._conn = storage.open_readonly(lookup_db)
        return self._conn

    def _serve_from_blobs(self, conn: sqlite3.Connection, rows: list[storage.AudioEntry], stem: str) -> Path | None:
        """Cache the first row whose blob can be read out of the android.db.

        Reuses the connection ``fetch`` already holds open on this file
        rather than opening a second one — for an android_db pack this is
        the same multi-GB file as the entry lookup.
        """
        assert self._blob_db_path is not None
        for row in rows:
            try:
                # length(data) first: an android_db pack's blob table can hold a
                # corrupt or mismatched multi-hundred-MB row, and checking the
                # stored size before touching the column avoids materializing
                # that whole blob into memory just to discard it (matches the
                # HTTP fetchers' MAX_AUDIO_BYTES abort in audio_fetch_common).
                size = conn.execute(
                    "SELECT length(data) FROM android WHERE file = ? AND source = ? ORDER BY id LIMIT 1",
                    (row.file, row.source),
                ).fetchone()
            except sqlite3.Error as exc:
                logger.debug("LocalAudioPackFetcher: blob size read failed for %s: %s", row.file, exc)
                continue
            if size is None or size[0] is None:
                continue
            if size[0] > MAX_AUDIO_BYTES:
                logger.debug(
                    "LocalAudioPackFetcher: skipping oversized android blob for %s (%d bytes)", row.file, size[0]
                )
                continue
            try:
                found = conn.execute(
                    "SELECT data FROM android WHERE file = ? AND source = ? ORDER BY id LIMIT 1",
                    (row.file, row.source),
                ).fetchone()
            except sqlite3.Error as exc:
                # An unreadable row is this row's problem, not the lookup's:
                # the next candidate may still serve.
                logger.debug("LocalAudioPackFetcher: blob read failed for %s: %s", row.file, exc)
                continue
            if found is None or not isinstance(found[0], bytes) or not found[0]:
                continue
            data = found[0]
            suffix = Path(row.file).suffix.lower() or ".mp3"

            def write_blob(part: Path, blob: bytes = data) -> None:
                part.write_bytes(blob)

            return self._write_cached(stem, suffix, write_blob)
        return None

    def _write_cached(self, stem: str, suffix: str, writer: Callable[[Path], object]) -> Path | None:
        """Stage *writer*'s bytes beside the cache and rename them into place."""
        cache_path = self._cache_dir / f"{stem}{suffix}"
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self._cache_dir, suffix=".part", delete=False) as tmp_fd:
                part_path = Path(tmp_fd.name)
            try:
                writer(part_path)
                os.replace(part_path, cache_path)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    part_path.unlink()
            _record_cached_path(self._cache_dir, cache_path)
        except OSError as exc:
            logger.debug("LocalAudioPackFetcher: cache write failed for %s: %s", cache_path, exc)
            return None
        return cache_path

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

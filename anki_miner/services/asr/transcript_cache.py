"""Saved transcripts, so a re-run never transcribes the same audio twice (F-12).

Transcribing an audiobook takes hours on a CPU. After a successful
transcription :func:`anki_miner.services.asr.long_audio.transcribe_media`
stores the segment list here; the next run on the same file with the same
model and language reads it back and skips extraction and transcription.
Utilities → Audiobook Sync and Utilities → Generate share the cache.

Layout: ``~/.anki_miner/cache/transcripts/<key>.json``, one file per
(media, model, language). The key hashes the media's resolved path, size,
mtime and first MiB, so an edited or replaced file misses. Each file is
written atomically; a file that is unreadable, of another format version or
malformed is a miss, never an error. :func:`prune_transcript_cache` keeps the
folder under ``TRANSCRIPT_CACHE_LIMIT_BYTES``, oldest first (a hit refreshes
a file's mtime). The cache is not settings: no export, profile, backup or
diagnostics bundle includes it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
from pathlib import Path

from anki_miner.config import paths
from anki_miner.utils.atomic_io import atomic_write_path

logger = logging.getLogger(__name__)

#: Bumped whenever what a transcription produces changes shape (MS-21's
#: re-break is part of version 1), so older entries miss instead of serving
#: stale segments.
CACHE_FORMAT = 1
#: Bytes of the media hashed into the key, besides its path, size and mtime.
HEAD_BYTES = 1024 * 1024
#: The folder is pruned to this size, oldest entries first (R16: 2 GB).
TRANSCRIPT_CACHE_LIMIT_BYTES = 2 * 1024**3

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

Segment = tuple[float, float, str]


def cache_root() -> Path:
    """The cache folder, read at call time so test home isolation applies."""
    return paths.ANKI_MINER_HOME / "cache" / "transcripts"


def cache_key(media_path: Path, *, model: str, language: str) -> str | None:
    """The cache key for ``media_path`` decoded by ``model`` in ``language``, or None when it cannot be read."""
    try:
        resolved = media_path.resolve()
        st = resolved.stat()
        with resolved.open("rb") as fh:
            head = fh.read(HEAD_BYTES)
    except OSError:
        return None
    digest = hashlib.sha256()
    digest.update(os.fsencode(resolved))
    digest.update(f"\0{st.st_size}\0{st.st_mtime_ns}\0".encode("ascii"))
    digest.update(head)
    return f"{digest.hexdigest()[:32]}-{_UNSAFE.sub('_', model)}-{_UNSAFE.sub('_', language)}"


def load_transcript(key: str) -> list[Segment] | None:
    """The stored segments for ``key``, or None on a miss (absent, unreadable, stale or malformed)."""
    path = cache_root() / f"{key}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("format") != CACHE_FORMAT:
        return None
    raw = payload.get("segments")
    if not isinstance(raw, list):
        return None
    segments: list[Segment] = []
    for item in raw:
        if (
            not isinstance(item, list)
            or len(item) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in item[:2])
            or not isinstance(item[2], str)
        ):
            return None
        segments.append((float(item[0]), float(item[1]), item[2]))
    with contextlib.suppress(OSError):
        os.utime(path)  # a hit is recent use: prune keeps it longer
    return segments


def store_transcript(key: str, segments: list[Segment]) -> None:
    """Save ``segments`` under ``key`` atomically. A failure is logged, never raised."""
    root = cache_root()
    payload = {"format": CACHE_FORMAT, "segments": [[s, e, t] for s, e, t in segments]}
    try:
        root.mkdir(parents=True, exist_ok=True)
        with atomic_write_path(root / f"{key}.json") as staged:
            staged.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("transcript_cache: could not save %s (%s)", key, exc)


def prune_transcript_cache(limit_bytes: int = TRANSCRIPT_CACHE_LIMIT_BYTES) -> int:
    """Delete the oldest files until the cache folder holds at most ``limit_bytes``; return how many went.

    Never raises: a file that cannot be read or deleted is skipped.
    """
    root = cache_root()
    entries: list[tuple[float, int, Path]] = []
    try:
        children = list(root.iterdir())
    except OSError:
        return 0
    for child in children:
        try:
            st = child.stat()
        except OSError:
            continue
        if child.is_file():
            entries.append((st.st_mtime, st.st_size, child))
    total = sum(size for _mtime, size, _path in entries)
    removed = 0
    for _mtime, size, path in sorted(entries, key=lambda e: e[0]):
        if total <= limit_bytes:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        removed += 1
    if removed:
        logger.info("transcript_cache: pruned %d file(s); %d bytes left", removed, total)
    return removed

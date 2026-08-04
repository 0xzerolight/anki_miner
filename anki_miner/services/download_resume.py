"""Durable partial-download state: a ``.part`` body plus an atomic manifest.

D16-C: a download that drops at 580 of 600 MB must not start again from zero.
The condition the owner attached to that decision is the whole risk here —
**resume proceeds only when the server proves the artifact is unchanged, and
silently restarts clean otherwise.** Appending bytes from a newer build of a
dictionary onto the prefix of an older one produces a file that passes a length
check and fails everything after it, so every ambiguity resolves to "throw the
partial away and fetch from byte zero".

What "proves unchanged" means here:

* a **strong** ``ETag`` (anything ``W/``-prefixed is weak and is treated as
  absent), or failing that a ``Last-Modified`` date — the two validators HTTP
  defines for ``If-Range``. With neither, no resume state is kept at all;
* the resumed response is exactly ``206`` with a ``Content-Range`` whose start
  equals our durable length and whose total equals the recorded total;
* the validator the ``206`` echoes still matches the recorded one;
* the response is unencoded (a ranged ``Content-Encoding`` would make byte
  offsets meaningless);
* the bytes we kept still hash to the digest recorded beside them.

A ``200``, ``412``, ``416``, a wrong range, a changed total, a changed or weak
validator, an encoded response or a prefix-hash mismatch all discard the partial.
A ``200`` is **never** appended to.

The manifest is bounded, versioned and replaced atomically; the body is fsynced
before the manifest that describes it, so a manifest never claims durable bytes
the filesystem does not have.

Storage lives under ``<anki_miner home>/runtime_state/downloads`` — deliberately
not under ``gui_config.json``'s serialisation and not under ``profiles/``, so it
can never travel in a settings export or a profile sidecar. See
:mod:`anki_miner.gui.utils.runtime_state`.

Qt-free and GUI-free: this module is consumed from ``anki_miner.services``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.bounded_reader import read_json_bounded

logger = logging.getLogger(__name__)

RUNTIME_STATE_DIRNAME = "runtime_state"
DOWNLOADS_DIRNAME = "downloads"

#: Bumped only when a stored manifest can no longer be read by this code. A
#: manifest carrying any other number is discarded, not migrated: the cost of
#: guessing wrong is a corrupt artifact, and the cost of being wrong the other
#: way is one re-download.
MANIFEST_VERSION = 1

#: A manifest is a handful of short strings. Anything larger is not one.
MANIFEST_MAX_BYTES = 64 * 1024

#: Flush + fsync + rewrite the manifest no more often than this. Small enough
#: that a crash costs seconds of transfer, large enough that a 600 MB download
#: is not 76,800 fsyncs.
CHECKPOINT_BYTES = 4 * 1024 * 1024
CHECKPOINT_SECONDS = 2.0

_UNREADABLE = object()

# A key names a file, so it may only contain characters that cannot escape the
# directory or collide across platforms. Callers supply stable literals; this is
# what stops a future caller's f-string from becoming a path traversal.
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def safe_key(key: str) -> str:
    """Return ``key`` unchanged, or raise when it cannot safely name a file.

    Raises:
        ValueError: If ``key`` is empty, over-long, or contains anything but
            ASCII alphanumerics, dot, dash and underscore — so no separator and
            no bare ``..``.
    """
    if key in {".", ".."} or not _SAFE_KEY.match(key):
        raise ValueError(f"unsafe download resume key: {key!r}")
    return key


def default_resume_root() -> Path:
    """Return ``<home>/runtime_state/downloads``, resolved at call time.

    Read through the module rather than a ``from … import`` binding so
    ``tests/_home_isolation.py``'s per-test retarget of
    ``anki_miner.config.paths.ANKI_MINER_HOME`` is honoured. A snapshot taken at
    import would keep writing into the user's real home and trip the
    ``guard_real_home`` tripwire.
    """
    from anki_miner.config import paths

    return paths.ANKI_MINER_HOME / RUNTIME_STATE_DIRNAME / DOWNLOADS_DIRNAME


def strong_validator(etag: str | None, last_modified: str | None) -> tuple[str | None, str | None]:
    """Reduce response validators to the pair worth resuming on.

    Returns ``(etag, last_modified)`` with a weak (``W/``-prefixed) or blank
    ETag dropped. ``(None, None)`` means the server proved nothing and no resume
    state may be kept.
    """
    tag = (etag or "").strip()
    if not tag or tag.startswith(("W/", "w/")):
        tag = ""
    modified = (last_modified or "").strip()
    return (tag or None, modified or None)


def is_identity_encoding(content_encoding: str | None) -> bool:
    """Whether a response body is stored bytes, so byte offsets mean anything."""
    encoding = (content_encoding or "").strip().lower()
    return encoding in {"", "identity"}


@dataclass(frozen=True)
class ResumeManifest:
    """What is durably on disk beside a ``.part`` body."""

    url: str
    total: int
    length: int
    sha256: str
    etag: str | None
    last_modified: str | None

    @property
    def if_range(self) -> str | None:
        """The ``If-Range`` value to send, preferring the strong ETag."""
        return self.etag or self.last_modified

    def to_json(self) -> dict[str, Any]:
        """Serialise for :data:`MANIFEST_VERSION`."""
        return {
            "version": MANIFEST_VERSION,
            "url": self.url,
            "total": self.total,
            "length": self.length,
            "sha256": self.sha256,
            "etag": self.etag,
            "last_modified": self.last_modified,
        }

    def matches_response(self, *, etag: str | None, last_modified: str | None) -> bool:
        """Whether a ranged response still describes the artifact we kept.

        A response that echoes a validator must echo *ours*. A response that
        echoes none is accepted only because the ``206`` itself is the server's
        answer to our ``If-Range`` — the ordinal checks on ``Content-Range``
        remain mandatory either way.
        """
        tag, modified = strong_validator(etag, last_modified)
        if tag is not None and tag != self.etag:
            # Either the entity tag moved, or the response names one where we
            # only ever had a date. Cheap to re-fetch; impossible to prove equal.
            return False
        return not (modified is not None and self.last_modified is not None and modified != self.last_modified)


@dataclass(frozen=True)
class RestoredPartial:
    """A verified partial: its manifest plus the live hasher over its bytes."""

    manifest: ResumeManifest
    hasher: hashlib._Hash


def parse_content_range(value: str | None) -> tuple[int, int, int] | None:
    """Parse ``bytes <start>-<end>/<total>`` into ints, or ``None``.

    A ``*`` total, a missing unit, a reversed range or any non-numeric field is
    reported as unparseable — which callers treat as "restart clean".
    """
    if not value:
        return None
    match = re.fullmatch(r"\s*bytes\s+(\d+)-(\d+)/(\d+)\s*", value)
    if match is None:
        return None
    start, end, total = (int(part) for part in match.groups())
    if end < start or total <= end:
        return None
    return start, end, total


class ResumeState:
    """The ``.part`` body and manifest for one stable download key."""

    def __init__(self, root: Path, key: str) -> None:
        """Bind to ``<root>/<key>.part`` and ``<root>/<key>.json``."""
        self._root = root
        self._key = safe_key(key)

    @property
    def key(self) -> str:
        """The validated key this state is stored under."""
        return self._key

    @property
    def part_path(self) -> Path:
        """Path of the partial body."""
        return self._root / f"{self._key}.part"

    @property
    def manifest_path(self) -> Path:
        """Path of the manifest describing :attr:`part_path`."""
        return self._root / f"{self._key}.json"

    def ensure_root(self) -> None:
        """Create the resume directory if it does not exist."""
        self._root.mkdir(parents=True, exist_ok=True)

    # -- reading ---------------------------------------------------------

    def load(self) -> ResumeManifest | None:
        """Return the stored manifest, or ``None`` when it is unusable.

        Every field is type-checked and every bound is enforced. A wrong schema
        version, a truncated file, an oversized file, a negative length or a
        length past the recorded total is reported as absent — the caller then
        restarts from byte zero, which is always correct.
        """
        raw = read_json_bounded(self.manifest_path, MANIFEST_MAX_BYTES, _UNREADABLE, "download resume manifest")
        if raw is _UNREADABLE or not isinstance(raw, dict):
            return None
        if raw.get("version") != MANIFEST_VERSION:
            return None
        url = raw.get("url")
        total = raw.get("total")
        length = raw.get("length")
        digest = raw.get("sha256")
        etag = raw.get("etag")
        last_modified = raw.get("last_modified")
        if not isinstance(url, str) or not url:
            return None
        # bool is an int subclass; a JSON `true` must not read as a byte count.
        if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
            return None
        if not isinstance(length, int) or isinstance(length, bool) or length <= 0 or length > total:
            return None
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return None
        if etag is not None and not isinstance(etag, str):
            return None
        if last_modified is not None and not isinstance(last_modified, str):
            return None
        etag, last_modified = strong_validator(etag, last_modified)
        if etag is None and last_modified is None:
            return None
        return ResumeManifest(
            url=url,
            total=total,
            length=length,
            sha256=digest,
            etag=etag,
            last_modified=last_modified,
        )

    def restore(self, url: str) -> RestoredPartial | None:
        """Return the kept prefix only if the bytes on disk still back it.

        Truncates the body to the durable length (bytes written after the last
        checkpoint were never fsynced and are not covered by the digest) and
        recomputes the prefix hash. Any mismatch discards the whole state. The
        live hasher comes back with the manifest so the caller can keep hashing
        the appended bytes without reading the prefix a second time.
        """
        attempted = self.part_path.exists() or self.manifest_path.exists()
        logger.debug(
            "Download resume: key=%s attempted=%s",
            self._key,
            attempted,
        )
        manifest = self.load()
        if manifest is None or manifest.url != url:
            logger.debug(
                "Download resume decision: key=%s safe=%s reason=%s",
                self._key,
                False,
                "manifest",
            )
            self.discard()
            return None
        try:
            size = self.part_path.stat().st_size
        except OSError:
            logger.debug(
                "Download resume decision: key=%s safe=%s reason=%s",
                self._key,
                False,
                "stat",
            )
            self.discard()
            return None
        if size < manifest.length:
            logger.debug(
                "Download resume decision: key=%s safe=%s reason=%s",
                self._key,
                False,
                "short",
            )
            self.discard()
            return None
        try:
            if size > manifest.length:
                with self.part_path.open("r+b") as handle:
                    handle.truncate(manifest.length)
            hasher = _prefix_hasher(self.part_path, manifest.length)
        except OSError:
            logger.debug(
                "Download resume decision: key=%s safe=%s reason=%s",
                self._key,
                False,
                "read",
            )
            self.discard()
            return None
        if hasher is None or hasher.hexdigest() != manifest.sha256:
            logger.debug(
                "Download resume decision: key=%s safe=%s reason=%s",
                self._key,
                False,
                "digest",
            )
            self.discard()
            return None
        logger.debug(
            "Download resume decision: key=%s safe=%s bytes=%d",
            self._key,
            True,
            manifest.length,
        )
        return RestoredPartial(manifest=manifest, hasher=hasher)

    # -- writing ---------------------------------------------------------

    def keepable(self, *, total: int, etag: str | None, last_modified: str | None) -> bool:
        """Whether a fresh (``200``) response is worth keeping a partial for.

        False when the server offered no strong validator or no length: there is
        then nothing a later resume could prove, so no partial is kept and the
        transfer behaves exactly as it did before D16-C.
        """
        etag, last_modified = strong_validator(etag, last_modified)
        return (etag is not None or last_modified is not None) and total > 0

    def checkpoint(
        self,
        handle: BinaryIO,
        *,
        url: str,
        total: int,
        length: int,
        digest: str,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        """Make ``length`` bytes durable, then atomically describe them.

        Order is the invariant: flush and ``fsync`` the body **first**, so the
        manifest can never claim bytes the filesystem has not committed. A
        manifest that lags the body only costs a few re-fetched kilobytes; a
        manifest that leads it corrupts the artifact.
        """
        handle.flush()
        os.fsync(handle.fileno())
        manifest = ResumeManifest(
            url=url,
            total=total,
            length=length,
            sha256=digest,
            etag=etag,
            last_modified=last_modified,
        )
        self.ensure_root()
        with atomic_write_path(self.manifest_path) as tmp:
            tmp.write_text(json.dumps(manifest.to_json()), encoding="utf-8")

    def discard(self) -> None:
        """Remove both files. Never raises."""
        part_existed = self.part_path.exists()
        for path in (self.manifest_path, self.part_path):
            # Best-effort cleanup of invalid resume state; restart remains safe.
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        if part_existed:
            logger.warning("Download partial discarded: key=%s", self._key)

    def drop_manifest(self) -> None:
        """Remove the manifest only, leaving the body to be promoted."""
        # Best-effort cleanup after the completed body is already durable.
        with contextlib.suppress(OSError):
            self.manifest_path.unlink(missing_ok=True)

    def promote(self, dest: Path) -> Path:
        """Move the completed body to ``dest`` and forget the manifest.

        ``os.replace`` when the two sit on one filesystem (the normal case —
        both live under the app home), ``shutil.move`` otherwise.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(self.part_path, dest)
        except OSError as exc:
            logger.debug(
                "Download promotion fallback: key=%s exc=%s",
                self._key,
                type(exc).__name__,
            )
            shutil.move(str(self.part_path), str(dest))
        self.drop_manifest()
        return dest


def _prefix_hasher(path: Path, length: int) -> hashlib._Hash | None:
    """Return a hasher fed the first ``length`` bytes of ``path``, or ``None``.

    ``None`` means the file is shorter than ``length`` — it cannot back the
    recorded prefix, so there is nothing to resume from.
    """
    digest = hashlib.sha256()
    remaining = length
    with path.open("rb") as handle:
        while remaining > 0:
            block = handle.read(min(1024 * 1024, remaining))
            if not block:
                return None
            remaining -= len(block)
            digest.update(block)
    return digest

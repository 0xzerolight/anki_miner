"""Shared micro-helpers for the in-app binary/pack installers.

The alass binary, ggml model, cuDNN/cuBLAS pack, and onnxruntime pack installers
all download to a ``.part`` temp, sha256-verify it, and atomically promote it.
The verify / cleanup / stale-sweep steps were byte-identical (or prefix-only
different) across those modules; they live here so hardening any one of them is a
single edit.

Not for ``resource_downloader`` (which owns the HTTP streaming and keeps its own
8 KiB read chunk); these helpers operate on already-downloaded files.
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
from pathlib import Path

from anki_miner.exceptions import SetupError

__all__ = ["CHUNK_SIZE", "verify_sha256", "cleanup_part", "sweep_stale"]

#: 1 MiB read chunks for streamed sha256 verification of a downloaded file.
CHUNK_SIZE = 1024 * 1024


def verify_sha256(path: Path, expected: str, what: str) -> None:
    """Stream *path* in chunks and raise ``SetupError`` on a sha256 mismatch.

    *what* is the user-facing subject of the error message (e.g. ``"alass
    download"``, ``"CUDA library download"``) so each caller keeps its own
    prefix.
    """
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise SetupError(f"{what} checksum mismatch: expected {expected}, got {actual}")


def cleanup_part(path: Path | None) -> None:
    """Remove *path* if it exists, ignoring errors."""
    if path is not None:
        with contextlib.suppress(OSError):
            path.unlink()


def sweep_stale(directory: Path) -> None:
    """Remove leftover ``.part`` files and ``.staging-*`` dirs from a crashed install.

    Best-effort: a missing dir or an unremovable entry is ignored. Only reclaims
    the download/extraction scratch artifacts; never touches promoted files.
    """
    with contextlib.suppress(OSError):
        for part in directory.glob("*.part"):
            with contextlib.suppress(OSError):
                part.unlink()
        for staging in directory.glob(".staging-*"):
            shutil.rmtree(staging, ignore_errors=True)

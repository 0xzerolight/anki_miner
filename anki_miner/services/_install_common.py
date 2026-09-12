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
import platform
import shutil
from pathlib import Path

from anki_miner.exceptions import SetupError

__all__ = ["CHUNK_SIZE", "verify_sha256", "cleanup_part", "sweep_stale", "macos_floor_met"]

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


def macos_floor_met(min_macos: tuple[int, int] | None) -> bool:
    """Return True when this machine satisfies a pinned wheel's macOS floor.

    A wheel tagged ``macosx_14_0_arm64`` cannot be loaded below macOS 14: dyld
    fails on symbols the older frameworks do not export, and that is an
    ImportError at engine startup, not a degraded feature. The floor is a
    property of the wheel we pinned, so it is declared on the spec
    (``ArtifactSpec.min_macos`` / ``_OnnxWheelSpec.min_macos``) and checked
    where an artifact is selected — an unusable wheel is then never offered nor
    downloaded, exactly as an unpinned platform is not.

    Fails OPEN: no floor (every Linux/Windows pin), a non-Darwin host, or a
    macOS release we cannot parse all pass. The alternative is hiding a working
    feature because a version string surprised us, and
    ``tests/unit/services/test_pinned_wheel_macos_floors.py`` is what keeps the
    declarations honest.
    """
    if min_macos is None or platform.system() != "Darwin":
        return True
    release = platform.mac_ver()[0]
    parts = release.split(".") if release else []
    try:
        current = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (IndexError, ValueError):
        return True
    if current == (10, 16):
        # Big Sur and later report 10.16 to a binary built against a pre-11 SDK
        # (the SYSTEM_VERSION_COMPAT shim). The real release is >= 11, so this
        # is not evidence of an old machine. The bundle's own CPython reports
        # truthfully; this is for pip installs on an older interpreter.
        return True
    return current >= min_macos

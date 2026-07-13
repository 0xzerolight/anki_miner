"""In-app installer for the ``alass`` subtitle-alignment binary.

Stateless, GUI-free service. Downloads the upstream kaegi/alass v2.0.0 release
asset for the current platform into a managed ``bin_root`` directory and places
it atomically. The HTTP+staging step is delegated to
``anki_miner.services.resource_downloader.download_to_temp`` (browser UA, chunked
streaming, size cap, ``.part`` staging); this module adds sha256 verification,
zip extraction (Windows), atomic placement, and the executable bit (POSIX).

macOS is unsupported: upstream publishes no v2.0.0 macOS binary, so a higher
layer shows Homebrew guidance instead. ``alass_install_supported()`` returns
False there and :func:`install_alass` raises ``SetupError``.

The placement mirrors the atomic-staging idiom in
``anki_miner.services.asr.model_manager.download``: write/extract to a temp path
*inside* ``bin_root`` (same filesystem), then ``os.replace`` to the final name so
no partial binary is ever visible. The downloaded ``.part`` temp file is always
removed (success or failure).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from anki_miner.exceptions import SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.services.resource_downloader import download_to_temp

logger = logging.getLogger(__name__)

__all__ = [
    "alass_install_supported",
    "alass_target_path",
    "is_installed",
    "install_alass",
]

_CHUNK_SIZE = 1024 * 1024  # 1 MiB chunks for streamed sha256.


@dataclass(frozen=True)
class _AlassSpec:
    """Per-platform download spec for the alass release asset."""

    url: str
    sha256: str
    is_zip: bool
    zip_member: str | None
    dest_name: str


# Upstream kaegi/alass v2.0.0 release assets. Checksums match the values pinned
# in .github/workflows/release.yml.
_LINUX_SPEC = _AlassSpec(
    url="https://github.com/kaegi/alass/releases/download/v2.0.0/alass-linux64",
    sha256="7bd0b9ae7e035d3ba940eacffb21243614df36231d47f21f0b4ce42001ab7fcd",
    is_zip=False,
    zip_member=None,
    dest_name="alass",
)

_WINDOWS_SPEC = _AlassSpec(
    url="https://github.com/kaegi/alass/releases/download/v2.0.0/alass-windows64.zip",
    sha256="e81a72f97f592910e909a2352d6b8c0de0801c51ac1383bad4ebf3f2ecdd2fd8",
    is_zip=True,
    # The v2.0.0 alass-windows64.zip nests the binary two dirs deep under a
    # top-level dir; the archive is sha256-pinned above so this path is stable.
    zip_member="alass-windows64/bin/alass-cli.exe",
    dest_name="alass.exe",
)


def _current_spec() -> _AlassSpec | None:
    """Return the spec for the current platform, or ``None`` if unsupported."""
    if sys.platform == "linux":
        return _LINUX_SPEC
    if sys.platform == "win32":
        return _WINDOWS_SPEC
    return None


def alass_install_supported() -> bool:
    """Return True when in-app alass download is supported on this platform.

    True on Linux and Windows; False elsewhere (notably macOS, which has no
    upstream v2.0.0 binary).
    """
    return _current_spec() is not None


def alass_target_path(bin_root: Path) -> Path:
    """Return the managed install path for alass under *bin_root*.

    ``bin_root/alass.exe`` on Windows, ``bin_root/alass`` otherwise. Used by
    callers to show installed/not-installed state without triggering a download.
    """
    name = "alass.exe" if sys.platform == "win32" else "alass"
    return bin_root / name


def is_installed(bin_root: Path) -> bool:
    """Return True if a managed alass binary is present and usable in *bin_root*.

    The target path must exist and be a regular file. On POSIX it must also be
    executable (a present-but-non-executable file would fail at subprocess time,
    so it does not count as installed). Cheap: a couple of stat calls.
    """
    target = alass_target_path(bin_root)
    if not target.is_file():
        return False
    if sys.platform != "win32" and not os.access(target, os.X_OK):
        return False
    return True


def install_alass(
    bin_root: Path,
    *,
    progress: DownloadProgressFn | None = None,
    cancel_event=None,
) -> Path:
    """Download, verify, and install the alass binary into *bin_root*.

    Orchestrates download -> sha256 verify -> atomic place -> chmod (POSIX) and
    returns the final binary path. The download is staged as a ``.part`` temp
    file inside *bin_root* and always removed afterwards.

    Args:
        bin_root: Managed directory for downloaded executables; created if
            missing. Typically ``config.bin_root``.
        progress: Optional ``(downloaded, total, message)`` callback threaded
            into the underlying download.
        cancel_event: Optional ``threading.Event``. Checked before the heavy
            steps; on cancellation no partial binary is placed and a
            ``SetupError`` is raised.

    Returns:
        Path to the installed binary (``bin_root/alass`` or ``bin_root/alass.exe``).

    Raises:
        SetupError: On unsupported platform, cancellation, download failure,
            sha256 mismatch, or a bad/missing zip member.
    """
    spec = _current_spec()
    if spec is None:
        raise SetupError(f"In-app alass install is not supported on this platform ({sys.platform}).")

    if cancel_event is not None and cancel_event.is_set():
        raise SetupError("alass installation cancelled")

    bin_root.mkdir(parents=True, exist_ok=True)

    cancelled_check = cancel_event.is_set if cancel_event is not None else None

    # Download to a .part temp file inside bin_root (same filesystem -> atomic
    # promotion). download_to_temp raises SetupError on failure/cancel.
    part_path = download_to_temp(
        spec.url,
        dest_dir=bin_root,
        progress=progress,
        cancelled_check=cancelled_check,
    )

    try:
        if cancel_event is not None and cancel_event.is_set():
            raise SetupError("alass installation cancelled")

        _verify_sha256(part_path, spec.sha256)

        target = bin_root / spec.dest_name
        if spec.is_zip:
            _place_zip_member(part_path, spec, target)
        else:
            _place_file(part_path, target)

        if sys.platform != "win32":
            os.chmod(target, 0o755)

        return target
    finally:
        _cleanup(part_path)


def _verify_sha256(path: Path, expected: str) -> None:
    """Stream *path* in chunks and raise ``SetupError`` on a sha256 mismatch."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise SetupError(f"alass download checksum mismatch: expected {expected}, got {actual}")


def _place_file(part_path: Path, target: Path) -> None:
    """Atomically move a bare downloaded binary into place at *target*."""
    staged = _staged_path(target)
    try:
        os.replace(part_path, staged)
    except OSError as exc:
        raise SetupError(f"Failed to stage alass binary: {exc}") from exc
    _promote(staged, target)


def _place_zip_member(part_path: Path, spec: _AlassSpec, target: Path) -> None:
    """Extract *spec.zip_member* from the downloaded zip and place it at *target*."""
    assert spec.zip_member is not None  # is_zip implies a member name.
    staged = _staged_path(target)
    try:
        with zipfile.ZipFile(part_path) as zf:
            try:
                member_bytes = zf.read(spec.zip_member)
            except KeyError as exc:
                raise SetupError(f"alass archive is missing expected entry '{spec.zip_member}'") from exc
        staged.write_bytes(member_bytes)
    except zipfile.BadZipFile as exc:
        with contextlib.suppress(OSError):
            staged.unlink()
        raise SetupError(f"alass download is not a valid zip archive: {exc}") from exc
    except SetupError:
        with contextlib.suppress(OSError):
            staged.unlink()
        raise
    except OSError as exc:
        with contextlib.suppress(OSError):
            staged.unlink()
        raise SetupError(f"Failed to write alass binary: {exc}") from exc
    _promote(staged, target)


def _staged_path(target: Path) -> Path:
    """Return a unique staging path beside *target* on the same filesystem."""
    fd, name = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
    os.close(fd)
    return Path(name)


def _promote(staged: Path, target: Path) -> None:
    """Atomically replace *target* with *staged*."""
    try:
        os.replace(staged, target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            staged.unlink()
        raise SetupError(f"Failed to install alass binary: {exc}") from exc


def _cleanup(path: Path | None) -> None:
    """Remove *path* if it exists, ignoring errors."""
    if path is not None:
        with contextlib.suppress(OSError):
            path.unlink()

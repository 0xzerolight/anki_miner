"""In-app installer for the onnxruntime pack (Silero VAD / silence removal).

Stateless, GUI-free service. faster-whisper's Silero VAD needs onnxruntime, but
the PyInstaller bundle strips onnxruntime (~57 MB) to stay slim — VAD is needed
only for silence removal, not core transcription. This module fetches onnxruntime
on demand so bundled-installer users can enable VAD without a rebundle. A pip
``[asr]`` install already pulls onnxruntime, so the pack is bundle-only.

Unlike the cuDNN/cuBLAS pack (``cuda_pack_installer``), which extracts a few
shared libs and ``dlopen``s them, onnxruntime is a *Python package*: the whole
``onnxruntime/`` tree is extracted (structure preserved) into
``onnx_pack_root/onnxruntime/``, and ``transcriber._ensure_onnx_pack_on_syspath``
adds ``onnx_pack_root`` to ``sys.path`` so ``import onnxruntime`` resolves.

The wheels are Python-ABI + platform + (on macOS) OS-version specific. The
release bundle ships CPython 3.12, so only ``cp312`` wheels are pinned;
``onnx_pack_supported`` returns False on any other interpreter (where the pack
would be ABI-incompatible — and where onnxruntime is anyway present from pip),
and on a macOS older than the pinned wheel's own ``macosx_*`` tag, which dyld
enforces absolutely. Bumping a version means updating that entry's url AND
sha256 together (alass-style).

Placement mirrors the atomic-staging idiom in ``cuda_pack_installer`` and
``model_manager.download``: members are extracted into a private staging dir
*inside* ``onnx_pack_root`` (same filesystem), then ``os.replace`` promotes the
``onnxruntime`` dir so no partial package is ever visible. The downloaded
``.part`` wheel is always removed (success, failure, or cancel).
"""

from __future__ import annotations

import logging
import platform
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.languages.pack_spec import macos_floor_from_url
from anki_miner.services._install_common import cleanup_part, macos_floor_met, sweep_stale, verify_sha256
from anki_miner.services.resource_downloader import download_to_temp
from anki_miner.utils.atomic_io import atomic_replace_dir, reconcile_dir
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

__all__ = [
    "onnx_pack_supported",
    "is_installed",
    "install_onnx_pack",
]

#: onnxruntime wheels are ~16-60 MB; cap generously below resource_downloader's
#: 600 MB default to fail fast on a wrong/oversized download.
_MAX_WHEEL_BYTES = 200 * 1024 * 1024

#: The CPython the release bundle ships (see .github/workflows/release.yml).
#: All pinned wheels target this ABI; keep in sync when the bundle Python bumps.
_BUNDLE_PYTHON = (3, 12)


@dataclass(frozen=True)
class _OnnxWheelSpec:
    """Download spec for one (platform, machine) onnxruntime wheel."""

    url: str
    sha256: str

    @property
    def min_macos(self) -> tuple[int, int] | None:
        """The macOS release this wheel's tag demands, read off the filename.

        Same rule and same reason as ``ArtifactSpec.min_macos``: a wheel tagged
        ``macosx_14_0`` cannot load below macOS 14, so ``_current_spec`` declines
        it rather than offering a ~30 MB download that ends in an ImportError.
        """
        return macos_floor_from_url(self.url)


# onnxruntime cp312 wheels, keyed by (sys.platform, platform.machine()).
# Bumping a version means updating BOTH url and sha256 for that entry.
#
# The table is VERSIONED PER PLATFORM on purpose. 1.27.0's arm64 macOS wheel is
# tagged macosx_14_0, which is a hard dyld floor, so Apple Silicon stays on
# 1.23.2 (macosx_13_0) — the newest release that still loads on Ventura. The
# table cannot be downpinned wholesale to match: 1.23.2 publishes no win_arm64
# cp312 wheel, so Windows ARM64 would lose silence removal entirely. macOS 11
# and 12 get no VAD either way (onnxruntime has shipped no arm64 wheel that low
# for years) and, thanks to min_macos, are no longer offered one.
_WHEELS: dict[tuple[str, str], _OnnxWheelSpec] = {
    ("linux", "x86_64"): _OnnxWheelSpec(
        url=(
            "https://files.pythonhosted.org/packages/26/81/"
            "24dd9b31b0fb912ee19ca53ac1c9764bfd79d58a2ccef564eb693be831a5/"
            "onnxruntime-1.27.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"
        ),
        sha256="7c65a7438632d55dfbc8a02ee60bd6cf7dd9d1ba05a43d4b851452f32338e194",
    ),
    ("linux", "aarch64"): _OnnxWheelSpec(
        url=(
            "https://files.pythonhosted.org/packages/84/86/"
            "c3b6b17745a1997d784dadc9bd88d713d2e6721139a5a0e885b28cfb79b1/"
            "onnxruntime-1.27.0-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl"
        ),
        sha256="c6fddce0539a4898c7bef35b052ffd37935b2190e35488eab99ce91887743ea1",
    ),
    ("win32", "AMD64"): _OnnxWheelSpec(
        url=(
            "https://files.pythonhosted.org/packages/4f/88/"
            "8ec9db1a4d126bb8b758992beb40d1249df171917d75f44a327eb5f20dda/"
            "onnxruntime-1.27.0-cp312-cp312-win_amd64.whl"
        ),
        sha256="20c321cf187ba496e648acf6b4cf90b4d398b0d17c2a77fdaeba365b908cc1c1",
    ),
    ("win32", "ARM64"): _OnnxWheelSpec(
        url=(
            "https://files.pythonhosted.org/packages/ae/9f/"
            "fdad359dfcba7e7cd8815569b304a596531d4efa77a75d77f8b4981891a2/"
            "onnxruntime-1.27.0-cp312-cp312-win_arm64.whl"
        ),
        sha256="d0d1f68868e2ef30ef70998ba9bbbc5c305e9b17041e3936751c1b8aa6aade06",
    ),
    ("darwin", "arm64"): _OnnxWheelSpec(
        url=(
            "https://files.pythonhosted.org/packages/1b/9e/"
            "f748cd64161213adeef83d0cb16cb8ace1e62fa501033acdd9f9341fff57/"
            "onnxruntime-1.23.2-cp312-cp312-macosx_13_0_arm64.whl"
        ),
        sha256="b8f029a6b98d3cf5be564d52802bb50a8489ab73409fa9db0bf583eabb7c2321",
    ),
    # ("darwin", "x86_64") stays unsupported: 1.27.0 ships no cp312 Intel-mac
    # wheel, and Intel-mac silence removal is a feature to add deliberately, not
    # a side effect of the arm64 downpin (1.23.2 does publish one).
}


def _current_spec() -> _OnnxWheelSpec | None:
    """Return the wheel spec for this platform/arch/Python, or ``None``.

    ``None`` when the interpreter is not the bundle's CPython (the wheel would be
    ABI-incompatible), no wheel is pinned for the platform/arch, or the pinned
    wheel's macOS tag demands a newer macOS than this machine runs — dyld would
    refuse it, so the row is hidden exactly as it is on Intel macOS rather than
    offering a download that ends in an ImportError.
    """
    if sys.version_info[:2] != _BUNDLE_PYTHON:
        return None
    spec = _WHEELS.get((sys.platform, platform.machine()))
    if spec is not None and not macos_floor_met(spec.min_macos):
        logger.debug(
            "onnxruntime pack: pinned wheel needs macOS %s, host reports %s; not offered",
            spec.min_macos,
            platform.mac_ver()[0] or "?",
        )
        return None
    return spec


def onnx_pack_supported() -> bool:
    """Return True when in-app onnxruntime-pack download is supported here.

    True only on the bundle's CPython and a platform/arch with a pinned wheel
    this machine can load. False elsewhere (other Python versions — where
    onnxruntime is already importable from pip — unsupported arches like Intel
    macOS, and an Apple Silicon Mac below the pinned wheel's macOS floor).
    """
    return _current_spec() is not None


def is_installed(onnx_pack_root: Path) -> bool:
    """Return True if a usable onnxruntime package is present in the pack dir.

    Cheap: a single ``__init__.py`` existence check on the extracted package.
    """
    target = onnx_pack_root / "onnxruntime"
    reconcile_dir(target)
    return (target / "__init__.py").exists()


def install_onnx_pack(
    onnx_pack_root: Path,
    *,
    progress: DownloadProgressFn | None = None,
    cancel_event=None,
) -> Path:
    """Download, verify, and install the onnxruntime package into *onnx_pack_root*.

    Downloads the platform wheel to a ``.part`` file inside *onnx_pack_root*,
    verifies its sha256, extracts the ``onnxruntime/`` tree into a fresh staging
    dir, and atomically ``os.replace``s it onto ``onnx_pack_root/onnxruntime``
    (replacing any existing copy). The ``.part`` wheel is always removed. A
    cancellation or any failure leaves nothing partial promoted.

    Args:
        onnx_pack_root: Managed directory for the pack; created if missing.
            Typically ``config.onnx_pack_root``.
        progress: Optional ``(downloaded, total, message)`` callback.
        cancel_event: Optional ``threading.Event``. Checked before each heavy
            step (download, verify, extract); on cancellation no partial package
            is promoted and a ``SetupError`` is raised.

    Returns:
        The *onnx_pack_root* path.

    Raises:
        SetupError: On unsupported platform/Python, cancellation, download
            failure, sha256 mismatch, or a bad/empty wheel.
    """
    spec = _current_spec()
    if spec is None:
        raise SetupError(
            f"In-app onnxruntime install is not supported on this platform/Python "
            f"({sys.platform}/{platform.machine()}/"
            f"{sys.version_info[0]}.{sys.version_info[1]})."
        )

    logger.info(
        "ONNX pack install: host=%s expected_bytes=%s",
        urlsplit(spec.url).hostname or "-",
        "-",
    )

    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled("onnxruntime installation cancelled")

    onnx_pack_root.mkdir(parents=True, exist_ok=True)
    # Reclaim orphans from a previous crashed/killed install (a hard kill between
    # download and os.replace leaves a .part wheel and/or a .staging-* dir). The
    # promoted onnxruntime/ tree is never touched, so is_installed is unaffected.
    sweep_stale(onnx_pack_root)
    cancelled_check = cancel_event.is_set if cancel_event is not None else None

    def _on_progress(downloaded: int, total: int, _msg: str) -> None:
        if progress is not None:
            progress(downloaded, total, "onnxruntime: downloading")

    part_path = download_to_temp(
        spec.url,
        dest_dir=onnx_pack_root,
        progress=_on_progress if progress is not None else None,
        cancelled_check=cancelled_check,
        max_bytes=_MAX_WHEEL_BYTES,
        # Keyed on the pinned checksum: a wheel bump changes the sha, so the
        # old partial can never be resumed into the new wheel (D16-C).
        resume_key=f"onnx-{spec.sha256[:16]}",
    )
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("onnxruntime installation cancelled")

        verify_sha256(part_path, spec.sha256, "onnxruntime download")

        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("onnxruntime installation cancelled")

        _extract_package(part_path, onnx_pack_root)
    finally:
        cleanup_part(part_path)

    target = onnx_pack_root / "onnxruntime"
    byte_count = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
    log_summary(
        logger,
        "ONNX pack install done",
        installed=target,
        bytes=byte_count,
    )
    return onnx_pack_root


def _safe_member_path(base: Path, member: str) -> Path:
    """Resolve *member* under *base*, rejecting path-traversal (zip slip)."""
    base_resolved = base.resolve()
    dest = (base / member).resolve()
    if base_resolved != dest and base_resolved not in dest.parents:
        raise SetupError(f"unsafe path in onnxruntime wheel: {member}")
    return dest


def _extract_package(part_path: Path, onnx_pack_root: Path) -> None:
    """Extract the ``onnxruntime/`` tree and atomically promote it.

    Members under ``onnxruntime/`` are written into a fresh staging dir with
    their relative structure preserved (the ``*.dist-info`` metadata is skipped —
    not needed for import). The staged ``onnxruntime`` dir then replaces
    ``onnx_pack_root/onnxruntime``.
    """
    target = onnx_pack_root / "onnxruntime"
    staging = Path(tempfile.mkdtemp(prefix=".staging-onnx-", dir=onnx_pack_root))
    try:
        try:
            with zipfile.ZipFile(part_path) as zf:
                members = [name for name in zf.namelist() if name.startswith("onnxruntime/") and not name.endswith("/")]
                if not members:
                    raise SetupError("onnxruntime wheel contained no onnxruntime/ package")
                for member in members:
                    dest = _safe_member_path(staging, member)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(member))
        except zipfile.BadZipFile as exc:
            logger.warning(
                "ONNX pack install failed: stage=extract exc=%s",
                type(exc).__name__,
            )
            raise SetupError(f"onnxruntime download is not a valid wheel: {exc}") from exc

        extracted_pkg = staging / "onnxruntime"
        if not (extracted_pkg / "__init__.py").exists():
            raise SetupError("onnxruntime wheel is missing onnxruntime/__init__.py")

        atomic_replace_dir(extracted_pkg, target)
    finally:
        # Best-effort cleanup on success or an already-failing extraction path.
        shutil.rmtree(staging, ignore_errors=True)

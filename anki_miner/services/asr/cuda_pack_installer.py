"""In-app installer for the cuDNN + cuBLAS shared-library pack (GPU/CUDA ASR).

Stateless, GUI-free service. The PyInstaller bundle already ships a CUDA-capable
``ctranslate2`` + ``faster-whisper`` but omits the heavy cuDNN/cuBLAS shared
libs; this module fetches them on demand so bundled-installer users can enable
GPU transcription without a rebundle. The libs land flattened in
``cuda_libs_root/cudnn/`` and ``cuda_libs_root/cublas/`` — exactly where
``transcriber._preload_cuda_libs`` (Task 1) looks for them.

Each component is a PyPI wheel (a zip) published by NVIDIA. We download the
per-platform wheel, verify its sha256, then extract every shared-lib member from
``nvidia/<component>/lib`` (Linux) or ``nvidia/<component>/bin`` (Windows),
flattening basenames into the component dir. macOS is unsupported (no CUDA on
Apple): :func:`cuda_pack_supported` returns False there and
:func:`install_cuda_pack` raises ``SetupError``.

The placement mirrors the atomic-staging idiom in
``anki_miner.services.alass_installer`` and
``anki_miner.services.asr.model_manager.download``: members are extracted into a
private staging dir *inside* ``cuda_libs_root`` (same filesystem), then
``os.replace`` promotes that dir onto ``cuda_libs_root/<component>`` so no
partial component is ever visible. The downloaded ``.part`` wheel is always
removed (success, failure, or cancel).

Versions are pinned ABI-compatible with the bundled ctranslate2 4.8 (CUDA 12 /
cuDNN 9). Bumping a version means updating its url AND sha256 together
(alass-style).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from anki_miner.exceptions import SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.services._install_common import cleanup_part, sweep_stale, verify_sha256
from anki_miner.services.resource_downloader import download_to_temp
from anki_miner.utils.atomic_io import atomic_replace_dir

logger = logging.getLogger(__name__)

__all__ = [
    "cuda_pack_supported",
    "is_installed",
    "install_cuda_pack",
]

#: The cuDNN wheel is ~688 MB, over resource_downloader's 600 MB default cap, so
#: every component download lifts the cap to this value.
_MAX_WHEEL_BYTES = 800 * 1024 * 1024


@dataclass(frozen=True)
class _CudaLibSpec:
    """Per-platform download + extraction spec for one CUDA component."""

    component: str  # "cudnn" / "cublas" — also the destination subdir name.
    url: str
    sha256: str
    member_prefix: str  # zip-internal dir whose direct children we extract.
    member_suffixes: tuple[str, ...]  # basename substrings marking a shared lib.


# NVIDIA PyPI wheels, pinned ABI-compatible with bundled ctranslate2 4.8
# (CUDA 12 / cuDNN 9). Bumping a version means updating BOTH url and sha256.
_LINUX_CUDNN_SPEC = _CudaLibSpec(
    component="cudnn",
    url=(
        "https://files.pythonhosted.org/packages/e1/ef/"
        "276c358c6ab44efbe68e69977657cb80927e7ab6d45968d042e1083f45e5/"
        "nvidia_cudnn_cu12-9.23.2.1-py3-none-manylinux_2_27_x86_64.whl"
    ),
    sha256="a5e706320218dc7d661b0e13402f204eeccd07b18d061b4d60668f80e464dd1e",
    member_prefix="nvidia/cudnn/lib/",
    member_suffixes=(".so",),
)

_WINDOWS_CUDNN_SPEC = _CudaLibSpec(
    component="cudnn",
    url=(
        "https://files.pythonhosted.org/packages/9b/93/"
        "b37f3a0fe29b1ae3bbb42c22cc25cb152971bb400a589cade336bdf5f4f3/"
        "nvidia_cudnn_cu12-9.23.2.1-py3-none-win_amd64.whl"
    ),
    sha256="549d6eb120cdd89429997243cd2cad1e864aac3a2f887a93f17836ce72d83873",
    member_prefix="nvidia/cudnn/bin/",
    member_suffixes=(".dll",),
)

_LINUX_CUBLAS_SPEC = _CudaLibSpec(
    component="cublas",
    url=(
        "https://files.pythonhosted.org/packages/cb/c0/"
        "0a517bfe63ccd3b92eb254d264e28fca3c7cab75d07daea315250fb1bf73/"
        "nvidia_cublas_cu12-12.9.2.10-py3-none-manylinux_2_27_x86_64.whl"
    ),
    sha256="e4f53a8ca8c5d6e8c492d0d0a3d565ecb59a751b19cfdaa4f6da0ab2104c1702",
    member_prefix="nvidia/cublas/lib/",
    member_suffixes=(".so",),
)

_WINDOWS_CUBLAS_SPEC = _CudaLibSpec(
    component="cublas",
    url=(
        "https://files.pythonhosted.org/packages/20/e2/"
        "fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
        "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl"
    ),
    sha256="623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661",
    member_prefix="nvidia/cublas/bin/",
    member_suffixes=(".dll",),
)


def _current_specs() -> tuple[_CudaLibSpec, ...] | None:
    """Return the (cuDNN, cuBLAS) specs for this platform, or ``None``."""
    if sys.platform == "linux":
        return (_LINUX_CUDNN_SPEC, _LINUX_CUBLAS_SPEC)
    if sys.platform == "win32":
        return (_WINDOWS_CUDNN_SPEC, _WINDOWS_CUBLAS_SPEC)
    return None


def cuda_pack_supported() -> bool:
    """Return True when in-app CUDA-pack download is supported on this platform.

    True on Linux and Windows; False elsewhere (notably macOS, which has no
    CUDA support on Apple hardware).
    """
    return _current_specs() is not None


def is_installed(cuda_libs_root: Path) -> bool:
    """Return True if the principal libs of BOTH components are present.

    Checks for the loader library of each component with a glob so a minor
    filename variation (a longer version suffix) does not break detection:

    * Linux: ``cudnn/libcudnn.so.9*`` AND ``cublas/libcublas.so.12*``.
    * Windows: ``cudnn/cudnn64_9.dll`` AND ``cublas/cublas64_12.dll``.

    Cheap: a couple of directory globs, no file reads.
    """
    if sys.platform == "win32":
        cudnn_ok = any((cuda_libs_root / "cudnn").glob("cudnn64_9.dll"))
        cublas_ok = any((cuda_libs_root / "cublas").glob("cublas64_12.dll"))
    else:
        cudnn_ok = any((cuda_libs_root / "cudnn").glob("libcudnn.so.9*"))
        cublas_ok = any((cuda_libs_root / "cublas").glob("libcublas.so.12*"))
    return cudnn_ok and cublas_ok


def install_cuda_pack(
    cuda_libs_root: Path,
    *,
    progress: DownloadProgressFn | None = None,
    cancel_event=None,
) -> Path:
    """Download, verify, and install the cuDNN + cuBLAS libs into *cuda_libs_root*.

    For each component (cuDNN, then cuBLAS): download the platform wheel to a
    ``.part`` file inside *cuda_libs_root*, verify its sha256, extract the
    shared-lib members into a fresh staging dir, and atomically ``os.replace``
    that staging dir onto ``cuda_libs_root/<component>`` (replacing any existing
    component dir). The ``.part`` wheel is always removed afterwards. A
    cancellation or any failure leaves nothing partial promoted.

    Args:
        cuda_libs_root: Managed directory for the downloaded libs; created if
            missing. Typically ``config.cuda_libs_root``.
        progress: Optional ``(downloaded, total, message)`` callback threaded
            into each component download. The message is prefixed with the
            component name.
        cancel_event: Optional ``threading.Event``. Checked before each heavy
            step (download, verify, extract); on cancellation no partial
            component is promoted and a ``SetupError`` is raised.

    Returns:
        The *cuda_libs_root* path.

    Raises:
        SetupError: On unsupported platform, cancellation, download failure,
            sha256 mismatch, or a bad/empty wheel.
    """
    specs = _current_specs()
    if specs is None:
        raise SetupError(f"In-app CUDA library install is not supported on this platform ({sys.platform}).")

    if cancel_event is not None and cancel_event.is_set():
        raise SetupError("CUDA library installation cancelled")

    cuda_libs_root.mkdir(parents=True, exist_ok=True)
    # Reclaim orphans from a previous crashed/killed install (a hard kill between
    # download and os.replace leaves a multi-hundred-MB .part wheel and/or a
    # .staging-* dir behind). is_installed only inspects cudnn/ and cublas/, so
    # these can't false-positive a partial install — they just accumulate.
    sweep_stale(cuda_libs_root)
    cancelled_check = cancel_event.is_set if cancel_event is not None else None

    for spec in specs:

        def _component_progress(downloaded: int, total: int, _msg: str, _c: str = spec.component) -> None:
            if progress is not None:
                progress(downloaded, total, f"{_c}: downloading")

        # Download to a .part wheel inside cuda_libs_root (same filesystem).
        part_path = download_to_temp(
            spec.url,
            dest_dir=cuda_libs_root,
            progress=_component_progress if progress is not None else None,
            cancelled_check=cancelled_check,
            max_bytes=_MAX_WHEEL_BYTES,
        )
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise SetupError("CUDA library installation cancelled")

            verify_sha256(part_path, spec.sha256, "CUDA library download")

            if cancel_event is not None and cancel_event.is_set():
                raise SetupError("CUDA library installation cancelled")

            _extract_component(part_path, spec, cuda_libs_root)
        finally:
            cleanup_part(part_path)

    return cuda_libs_root


def _extract_component(part_path: Path, spec: _CudaLibSpec, cuda_libs_root: Path) -> None:
    """Extract *spec*'s shared-lib members and atomically promote the dir.

    Members directly under ``spec.member_prefix`` whose basename contains one of
    ``spec.member_suffixes`` are written, flattened (basename only), into a
    fresh staging dir, which then replaces ``cuda_libs_root/<component>``.
    """
    target = cuda_libs_root / spec.component
    staging = Path(tempfile.mkdtemp(prefix=f".staging-{spec.component}-", dir=cuda_libs_root))
    try:
        try:
            with zipfile.ZipFile(part_path) as zf:
                members = _select_members(zf.namelist(), spec)
                if not members:
                    raise SetupError(f"CUDA {spec.component} wheel contained no expected shared-lib members")
                for member in members:
                    basename = os.path.basename(member)
                    (staging / basename).write_bytes(zf.read(member))
        except zipfile.BadZipFile as exc:
            raise SetupError(f"CUDA {spec.component} download is not a valid wheel: {exc}") from exc

        atomic_replace_dir(staging, target)
        staging = None  # type: ignore[assignment]  # promoted; do not rmtree.
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def _select_members(names: list[str], spec: _CudaLibSpec) -> list[str]:
    """Return wheel members directly under the prefix that look like shared libs."""
    selected: list[str] = []
    for name in names:
        if not name.startswith(spec.member_prefix):
            continue
        basename = os.path.basename(name)
        if not basename:  # directory entry
            continue
        if any(sfx in basename for sfx in spec.member_suffixes):
            selected.append(name)
    return selected

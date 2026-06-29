"""In-app installer for whisper.cpp ggml model files (pywhispercpp backend).

Stateless, GUI-free service. The Vulkan/whisper.cpp ASR backend loads quantized
ggml model files directly off disk: a per-model acoustic weight file plus a
shared Silero VAD file. This module fetches them on demand — mirroring
``cuda_pack_installer`` / ``onnx_pack_installer`` — so users can enable GPU
transcription without bundling multi-GB weights.

Unlike those packs (each a wheel/zip whose members are extracted), a ggml model
IS a single ``.bin`` file: we download it to a ``.part`` temp inside the
``ggml/`` dir, verify its sha256, then ``os.replace`` the ``.part`` straight
onto its final ``ggml/<filename>.bin`` name (same filesystem → atomic). No
partial file is ever visible to the presence checks, and the ``.part`` is always
removed on failure or cancel.

Everything lives under ``asr_models_root / "ggml"``: ``ggml_model_path`` and
``vad_model_path`` resolve the on-disk names that Phase A4's transcriber points
pywhispercpp at. The valid ``asr_model`` set is reused from
``model_manager.KNOWN_MODELS`` (do not fork it). Bumping a model means updating
its url AND sha256 together (alass-style).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from anki_miner.exceptions import SetupError
from anki_miner.services.asr import model_manager
from anki_miner.services.resource_downloader import download_to_temp

logger = logging.getLogger(__name__)

__all__ = [
    "ggml_models_root",
    "ggml_model_path",
    "vad_model_path",
    "is_ggml_downloaded",
    "is_vad_downloaded",
    "install_ggml_model",
    "install_vad_model",
]

ProgressCallback = Callable[[int, int, str], None]

_CHUNK_SIZE = 1024 * 1024  # 1 MiB chunks for streamed sha256.

#: The largest pinned file (large-v3 acoustic) is ~1.03 GB, over
#: resource_downloader's 600 MB default cap, so every download lifts the cap to
#: this value (comfortably above 1.03 GB, still guarding a runaway response).
_MAX_BYTES = 1300 * 1024 * 1024


@dataclass(frozen=True)
class _GgmlSpec:
    """Download spec for one ggml ``.bin`` file."""

    filename: str  # destination basename inside the ggml/ dir.
    url: str
    sha256: str
    size_bytes: int


# whisper.cpp acoustic weights, keyed by asr_model (== model_manager.KNOWN_MODELS).
# Bumping a model means updating BOTH url and sha256.
_ACOUSTIC_SPECS: dict[str, _GgmlSpec] = {
    "large-v3": _GgmlSpec(
        filename="ggml-large-v3-q5_0.bin",
        url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-q5_0.bin",
        sha256="d75795ecff3f83b5faa89d1900604ad8c780abd5739fae406de19f23ecd98ad1",
        size_bytes=1081140203,
    ),
    "small": _GgmlSpec(
        filename="ggml-small-q5_1.bin",
        url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin",
        sha256="ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
        size_bytes=190085487,
    ),
}

# Silero VAD ggml file, shared across acoustic models.
_VAD_SPEC = _GgmlSpec(
    filename="ggml-silero-v6.2.0.bin",
    url="https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin",
    sha256="2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
    size_bytes=885098,
)


def ggml_models_root(asr_models_root: Path) -> Path:
    """Return the ``ggml/`` subdir of *asr_models_root* (where ggml files live)."""
    return asr_models_root / "ggml"


def _acoustic_spec(asr_model: str) -> _GgmlSpec:
    """Return the acoustic spec for *asr_model* or raise ``SetupError``."""
    spec = _ACOUSTIC_SPECS.get(asr_model)
    if spec is None:
        raise SetupError(f"Unknown ASR model {asr_model!r}; expected one of {sorted(model_manager.KNOWN_MODELS)}")
    return spec


def ggml_model_path(asr_model: str, asr_models_root: Path) -> Path:
    """Return the on-disk path of *asr_model*'s acoustic ggml file.

    Raises:
        SetupError: If *asr_model* is not a known model.
    """
    return ggml_models_root(asr_models_root) / _acoustic_spec(asr_model).filename


def vad_model_path(asr_models_root: Path) -> Path:
    """Return the on-disk path of the shared Silero VAD ggml file."""
    return ggml_models_root(asr_models_root) / _VAD_SPEC.filename


def _is_present(path: Path) -> bool:
    """Return True if *path* is a non-empty regular file (complete download).

    A zero-byte file is treated as absent: an interrupted external copy can
    leave a truncated stub that would then fail at load time. ``install`` always
    promotes atomically, so in normal operation this is defense-in-depth.
    """
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def is_ggml_downloaded(asr_model: str, asr_models_root: Path) -> bool:
    """Return True if *asr_model*'s acoustic ggml file is present and complete."""
    return _is_present(ggml_model_path(asr_model, asr_models_root))


def is_vad_downloaded(asr_models_root: Path) -> bool:
    """Return True if the shared Silero VAD ggml file is present and complete."""
    return _is_present(vad_model_path(asr_models_root))


def install_ggml_model(
    asr_model: str,
    asr_models_root: Path,
    *,
    progress: ProgressCallback | None = None,
    cancel_event=None,
) -> Path:
    """Download, verify, and install *asr_model*'s acoustic ggml file.

    Skips the download when the file is already present and complete. Otherwise
    downloads to a ``.part`` temp inside the ``ggml/`` dir, verifies its sha256,
    and atomically ``os.replace``s it onto its final ``ggml/<filename>`` name. A
    cancellation or any failure leaves no partial file promoted, and the
    ``.part`` is always removed.

    Args:
        asr_model: Model identifier (must be in ``model_manager.KNOWN_MODELS``).
        asr_models_root: Base dir; ggml files land in its ``ggml/`` subdir
            (created if missing). Typically ``config.asr_models_root``.
        progress: Optional ``(downloaded, total, message)`` callback.
        cancel_event: Optional ``threading.Event``. Checked before each heavy
            step (download, verify, promote); on cancellation nothing partial is
            promoted and a ``SetupError`` is raised.

    Returns:
        The path to the installed acoustic ggml file.

    Raises:
        SetupError: On an unknown model, cancellation, download failure, or
            sha256 mismatch.
    """
    spec = _acoustic_spec(asr_model)
    return _install_spec(spec, asr_models_root, progress=progress, cancel_event=cancel_event)


def install_vad_model(
    asr_models_root: Path,
    *,
    progress: ProgressCallback | None = None,
    cancel_event=None,
) -> Path:
    """Download, verify, and install the shared Silero VAD ggml file.

    Same atomic, fail-closed semantics as :func:`install_ggml_model`. The
    signature matches ``install_cuda_pack`` / ``install_onnx_pack`` so the GUI
    worker can drive it identically.

    Args:
        asr_models_root: Base dir; the VAD file lands in its ``ggml/`` subdir.
        progress: Optional ``(downloaded, total, message)`` callback.
        cancel_event: Optional ``threading.Event``.

    Returns:
        The path to the installed VAD ggml file.

    Raises:
        SetupError: On cancellation, download failure, or sha256 mismatch.
    """
    return _install_spec(_VAD_SPEC, asr_models_root, progress=progress, cancel_event=cancel_event)


def _install_spec(
    spec: _GgmlSpec,
    asr_models_root: Path,
    *,
    progress: ProgressCallback | None,
    cancel_event,
) -> Path:
    """Shared download/verify/promote path for one ggml file spec."""
    target = ggml_models_root(asr_models_root) / spec.filename

    # Skip when already present and complete; nothing to download.
    if _is_present(target):
        return target

    if cancel_event is not None and cancel_event.is_set():
        raise SetupError(f"ggml model installation cancelled ({spec.filename})")

    ggml_dir = ggml_models_root(asr_models_root)
    ggml_dir.mkdir(parents=True, exist_ok=True)
    # Reclaim orphans from a previous crashed/killed install (a hard kill between
    # download and os.replace leaves a large .part behind). The promoted .bin
    # files are never touched, so the presence checks are unaffected.
    _sweep_stale(ggml_dir)
    cancelled_check = cancel_event.is_set if cancel_event is not None else None

    def _on_progress(downloaded: int, total: int, _msg: str) -> None:
        if progress is not None:
            progress(downloaded, total, f"{spec.filename}: downloading")

    part_path = download_to_temp(
        spec.url,
        dest_dir=ggml_dir,
        progress=_on_progress if progress is not None else None,
        cancelled_check=cancelled_check,
        max_bytes=_MAX_BYTES,
    )
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise SetupError(f"ggml model installation cancelled ({spec.filename})")

        _verify_sha256(part_path, spec.sha256)

        if cancel_event is not None and cancel_event.is_set():
            raise SetupError(f"ggml model installation cancelled ({spec.filename})")

        # Promote the verified .part onto its final name atomically.
        os.replace(part_path, target)
        part_path = None  # type: ignore[assignment]  # promoted; do not unlink.
    finally:
        _cleanup(part_path)

    return target


def _verify_sha256(path: Path, expected: str) -> None:
    """Stream *path* in chunks and raise ``SetupError`` on a sha256 mismatch."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise SetupError(f"ggml model download checksum mismatch: expected {expected}, got {actual}")


def _sweep_stale(ggml_dir: Path) -> None:
    """Remove leftover ``.part`` files from a crashed install.

    Best-effort: a missing dir or an unremovable entry is ignored. Never touches
    the promoted ``.bin`` model files.
    """
    with contextlib.suppress(OSError):
        for part in ggml_dir.glob("*.part"):
            with contextlib.suppress(OSError):
                part.unlink()


def _cleanup(path: Path | None) -> None:
    """Remove *path* if it exists, ignoring errors."""
    if path is not None:
        with contextlib.suppress(OSError):
            path.unlink()

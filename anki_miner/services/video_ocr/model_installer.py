"""In-app installer for the two pinned meikiocr models (Utilities → Video OCR).

Stateless, GUI-free. Each model is one .onnx file: downloaded to a ``.part`` in
``models_root``, sha256-verified, then ``os.replace``d onto its final name (same
filesystem, atomic), so no partial file is ever visible to :func:`is_installed`.
This deliberately copies the ggml installer's skeleton instead of sharing it:
each sibling installer owns its download path and its tests patch it by module.
Bumping a model means updating url, sha256 and size together.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from anki_miner.exceptions import OperationCancelled
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.services._install_common import cleanup_part, sweep_stale, verify_sha256
from anki_miner.services.resource_downloader import download_to_temp
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

__all__ = ["DET_MODEL_NAME", "REC_MODEL_NAME", "MODEL_SPECS", "is_installed", "install_models"]

DET_MODEL_NAME = "meiki.text.detect.v0.1.960x544.onnx"
REC_MODEL_NAME = "meiki.text.rec.v0.960x32.onnx"

#: Both files are under 20 MB; the cap fails fast on a wrong or runaway response.
_MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class _ModelSpec:
    filename: str
    url: str
    sha256: str
    size_bytes: int


MODEL_SPECS: tuple[_ModelSpec, ...] = (
    _ModelSpec(
        filename=DET_MODEL_NAME,
        url=(
            "https://huggingface.co/rtr46/meiki.text.detect.v0/resolve/"
            f"a9cffa4f60cbf72ddb87edf19c6f98a01cd042e6/{DET_MODEL_NAME}"
        ),
        sha256="40b6a016667745cae7d3055929ae3b8b1e7716aac795f5904cd3c2c7c3b8404b",
        size_bytes=14503825,
    ),
    _ModelSpec(
        filename=REC_MODEL_NAME,
        url=(
            "https://huggingface.co/rtr46/meiki.txt.recognition.v0/resolve/"
            f"a28cf5874dc2438ebb1c86336be26bcec51e3375/{REC_MODEL_NAME}"
        ),
        sha256="3e96bc772fbee9717e536a6353032bb944c3382dd2f6960ef4890decda43b000",
        size_bytes=18593254,
    ),
)


def _present(path: Path, size_bytes: int) -> bool:
    try:
        return path.stat().st_size == size_bytes
    except OSError:
        return False


def is_installed(models_root: Path) -> bool:
    """True when both pinned models are on disk at their exact size (no import, no hashing)."""
    return all(_present(models_root / spec.filename, spec.size_bytes) for spec in MODEL_SPECS)


def install_models(
    models_root: Path,
    *,
    progress: DownloadProgressFn | None = None,
    cancel_event=None,
) -> Path:
    """Download, verify and install every missing model. Raises ``SetupError`` (incl. cancel)."""
    for spec in MODEL_SPECS:
        _install_one(spec, models_root, progress=progress, cancel_event=cancel_event)
    return models_root


def _raise_if_cancelled(cancel_event, spec: _ModelSpec) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled(f"OCR model installation cancelled ({spec.filename})")


def _install_one(spec: _ModelSpec, models_root: Path, *, progress, cancel_event) -> None:
    target = models_root / spec.filename
    if _present(target, spec.size_bytes):
        return
    _raise_if_cancelled(cancel_event, spec)
    models_root.mkdir(parents=True, exist_ok=True)
    sweep_stale(models_root)

    def _on_progress(downloaded: int, total: int, _msg: str) -> None:
        if progress is not None:
            progress(downloaded, total, f"{spec.filename}: downloading")

    part_path: Path | None = download_to_temp(
        spec.url,
        dest_dir=models_root,
        progress=_on_progress if progress is not None else None,
        cancelled_check=cancel_event.is_set if cancel_event is not None else None,
        max_bytes=_MAX_BYTES,
        # Keyed on the pinned checksum: a model bump can never resume the old partial.
        resume_key=f"ocr-{spec.sha256[:16]}",
    )
    try:
        _raise_if_cancelled(cancel_event, spec)
        assert part_path is not None
        verify_sha256(part_path, spec.sha256, "OCR model download")
        _raise_if_cancelled(cancel_event, spec)
        os.replace(part_path, target)
        part_path = None
    finally:
        cleanup_part(part_path)
    log_summary(logger, "OCR model install done", installed=target, bytes=target.stat().st_size)

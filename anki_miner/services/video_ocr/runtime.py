"""Where the Video OCR engine comes from: the availability probe and one shared, lazily loaded instance.

numpy-free at import: the engine module (numpy, PIL arrays) loads inside get_engine.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from anki_miner.services.asr import onnx_pack_installer

if TYPE_CHECKING:
    from anki_miner.services.video_ocr.meiki_engine import MeikiEngine

_lock = threading.Lock()
_cached: tuple[Path, MeikiEngine] | None = None


def runtime_ready(onnx_pack_root: Path) -> bool:
    """True when onnxruntime imports, from pip or the in-app pack (no model load)."""
    return onnx_pack_installer.onnxruntime_importable(onnx_pack_root)


def get_engine(onnx_pack_root: Path, models_root: Path) -> MeikiEngine:
    """The shared engine for ``models_root``, loaded on first use. Raises ``EngineLoadError``."""
    global _cached
    with _lock:
        if _cached is not None and _cached[0] == models_root:
            return _cached[1]
        onnx_pack_installer.ensure_on_syspath(onnx_pack_root)
        from anki_miner.services.video_ocr import meiki_engine

        engine = meiki_engine.load_engine(models_root)
        _cached = (models_root, engine)
        return engine


def _clear_cache() -> None:
    """Drop the shared engine (tests)."""
    global _cached
    with _lock:
        _cached = None

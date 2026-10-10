"""Where the Video OCR engine comes from: the availability probe and one shared, lazily loaded reader.

numpy-free at import: the engine modules (numpy, PIL arrays) and ``text_cleanup`` (its
``OcrBox`` comes from ``meiki_engine``) load inside the functions that use them.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anki_miner.languages.profile import LanguageProfile
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.asr import onnx_pack_installer
from anki_miner.services.ocr import model_installer
from anki_miner.services.ocr.model_installer import ModelSpec
from anki_miner.services.ocr.recognizers import RECOGNIZERS

_lock = threading.Lock()
#: ((models_root, recogniser key), engine): one engine at a time, replaced on a language switch.
_cached: tuple[tuple[Path, str], Any] | None = None


@dataclass(frozen=True)
class _Reader:
    engine: Any  # read_boxes(bgr) -> list[OcrBox]
    assemble: Callable[[Sequence[Any]], str]

    def read_text(self, bgr: Any) -> str:
        return self.assemble(self.engine.read_boxes(bgr))


def runtime_ready(onnx_pack_root: Path) -> bool:
    """True when onnxruntime imports, from pip or the in-app pack (no model load)."""
    return onnx_pack_installer.onnxruntime_importable(onnx_pack_root)


def required_models(profile: LanguageProfile) -> tuple[ModelSpec, ...]:
    """The pinned files Video OCR needs for ``profile``: meikiocr's pair, or its detector + the recogniser."""
    meiki = model_installer.MEIKI_SPECS  # read at call time, like the installer's own default
    key = profile.ocr_recognizer
    return meiki if not key else (meiki[0], RECOGNIZERS[key].model)


def _engine(onnx_pack_root: Path, models_root: Path, key: str) -> Any:
    global _cached
    with _lock:
        if _cached is not None and _cached[0] == (models_root, key):
            return _cached[1]
        onnx_pack_installer.ensure_on_syspath(onnx_pack_root)
        if key:
            from anki_miner.services.video_ocr import paddle_engine

            engine: Any = paddle_engine.load_engine(models_root, RECOGNIZERS[key])
        else:
            from anki_miner.services.video_ocr import meiki_engine

            engine = meiki_engine.load_engine(models_root)
        _cached = ((models_root, key), engine)
        return engine


def get_reader(config: Any) -> _Reader:
    """Frame crop → text for the mining language: the shared engine plus that language's assembly.

    Japanese (no recogniser) keeps meikiocr and :func:`text_cleanup.clean`. Every other
    language joins rows with its word separator and direction. Raises ``EngineLoadError``.
    """
    from anki_miner.services.video_ocr.text_cleanup import clean, join_rows

    profile = get_profile(config_language(config))
    key = profile.ocr_recognizer
    engine = _engine(config.onnx_pack_root, config.video_ocr_models_root, key)
    if not key:
        return _Reader(engine, clean)
    joiner = " " if profile.sentence_rules.space_aware else ""
    rtl = profile.content_style.direction == "rtl"
    return _Reader(engine, functools.partial(join_rows, joiner=joiner, rtl=rtl))


def _clear_cache() -> None:
    """Drop the shared engine (tests)."""
    global _cached
    with _lock:
        _cached = None

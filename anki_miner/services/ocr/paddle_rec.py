"""PP-OCR text-line recogniser (PaddleOCR models in RapidOCR's ONNX conversion) on onnxruntime.

Standalone: imports the standard library, numpy, PIL and (lazily) onnxruntime, never
``anki_miner``. The app loads it for Video OCR; the managed mokuro environment loads
it through ``mokuro_driver.py`` for Manga OCR.

Pre/post-processing follow RapidOCR v3.10.0 ``ch_ppocr_rec`` (Apache-2.0): BGR in,
height 48, width from the batch's widest aspect ratio (at least 320/48), scaled to
[-1, 1] and zero-padded on the right; greedy CTC over ``[blank, *character, " "]``,
``character`` being the list RapidOCR stores in the model's metadata.
``_visual_to_logical`` adapts PaddleOCR's ``BaseRecLabelDecode.pred_reverse``
(Apache-2.0) for the Arabic-script models, which emit text in visual order.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_HEIGHT = 48
_MIN_RATIO = 320 / 48
_MAX_BATCH = 8
# Characters that keep their order inside a reversed right-to-left line: Latin, ASCII
# and Arabic-Indic digits, and number punctuation. Unlike PaddleOCR, the space is not
# one of them, so it stays a word gap instead of moving to the end of a number.
_LTR_RUN = re.compile(r"[a-zA-Z0-9٠-٩۰-۹:*./%+\-]")
# A right-to-left line draws each bracket mirrored, so the glyph read as "(" is a logical ")".
_MIRROR = str.maketrans("()[]{}<>«»", ")(][}{><»«")


def _visual_to_logical(text: str) -> str:
    parts: list[str] = []
    run = ""
    for ch in text:
        if _LTR_RUN.match(ch):
            run += ch
            continue
        if run:
            parts.append(run)
            run = ""
        parts.append(ch.translate(_MIRROR))
    if run:
        parts.append(run)
    return "".join(reversed(parts))


def _normalise(bgr: np.ndarray, width: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    resized_w = max(1, min(width, math.ceil(_HEIGHT * w / max(1, h))))
    resized = Image.fromarray(np.ascontiguousarray(bgr)).resize((resized_w, _HEIGHT), Image.Resampling.BILINEAR)
    out = np.zeros((3, _HEIGHT, width), dtype=np.float32)
    out[:, :, :resized_w] = (np.asarray(resized, dtype=np.float32).transpose(2, 0, 1) / 255.0 - 0.5) / 0.5
    return out


class PaddleRecognizer:
    """One onnxruntime session over a PP-OCR recognition model."""

    def __init__(self, session: Any, *, reverse: bool = False) -> None:
        self._session = session
        self._input = session.get_inputs()[0].name
        self._chars = ["", *session.get_modelmeta().custom_metadata_map["character"].splitlines(), " "]
        self._reverse = reverse

    def recognize(self, images: Sequence[np.ndarray]) -> list[tuple[str, float]]:
        """Read one text line per BGR uint8 image: ``(text, mean confidence)`` in input order."""
        results: list[tuple[str, float]] = []
        for start in range(0, len(images), _MAX_BATCH):
            batch = images[start : start + _MAX_BATCH]
            ratio = max([_MIN_RATIO, *(img.shape[1] / max(1, img.shape[0]) for img in batch)])
            width = int(_HEIGHT * ratio)
            probs = self._session.run(None, {self._input: np.stack([_normalise(img, width) for img in batch])})[0]
            results.extend(self._decode(row) for row in probs)
        return results

    def _decode(self, probs: np.ndarray) -> tuple[str, float]:
        ids = probs.argmax(axis=1)
        keep = np.ones(len(ids), dtype=bool)
        keep[1:] = ids[1:] != ids[:-1]
        keep &= ids != 0
        if not keep.any():
            return "", 0.0
        text = "".join(self._chars[i] for i in ids[keep] if i < len(self._chars))
        if self._reverse:
            text = _visual_to_logical(text)
        return text, float(probs.max(axis=1)[keep].mean())


def load_recognizer(model_path: Path, *, reverse: bool = False) -> PaddleRecognizer:
    """Open ``model_path`` on CPU (the session options Video OCR's meikiocr engine uses)."""
    import onnxruntime as ort

    ort.set_default_logger_severity(3)
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
    return PaddleRecognizer(session, reverse=reverse)

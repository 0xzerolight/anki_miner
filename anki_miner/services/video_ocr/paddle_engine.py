"""Video OCR for the languages meikiocr cannot read: meikiocr's line detector + a PP-OCR recogniser.

The detector is generic axis-aligned text-line boxes (the 2026-10-10 spike measured it per
script); recognition is ``services.ocr.paddle_rec``. Boxes are padded before recognition
because PP-OCR's recognisers were trained on crops with a margin.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

from anki_miner.services.ocr import paddle_rec
from anki_miner.services.ocr.model_installer import DET_MODEL_NAME
from anki_miner.services.ocr.recognizers import RecognizerSpec
from anki_miner.services.video_ocr.errors import EngineLoadError
from anki_miner.services.video_ocr.meiki_engine import OcrBox, detect_lines, open_session

logger = logging.getLogger(__name__)

#: Margin added above, below and beside each box, as a fraction of its height. The spike's
#: mean video CER: 1.64% unpadded, 1.32% at 0.1, 1.33% at 0.2.
_BOX_PAD = 0.1


class PaddleEngine:
    """meikiocr's detector session + a :class:`paddle_rec.PaddleRecognizer`, behind one lock."""

    def __init__(self, det_session: Any, recognizer: Any) -> None:
        self._det = det_session
        self._rec = recognizer
        self._lock = threading.Lock()

    def read_boxes(self, bgr: np.ndarray) -> list[OcrBox]:
        """Detect and read every text line in ``bgr`` (H x W x 3, uint8), top to bottom."""
        h, w = bgr.shape[:2]
        with self._lock:
            boxes = detect_lines(self._det, bgr)
            crops = []
            for x1, y1, x2, y2 in boxes:
                pad = int(round((y2 - y1) * _BOX_PAD))
                crops.append(
                    np.ascontiguousarray(bgr[max(0, y1 - pad) : min(h, y2 + pad), max(0, x1 - pad) : min(w, x2 + pad)])
                )
            read = self._rec.recognize(crops) if crops else []
        return [OcrBox(text, box, conf) for box, (text, conf) in zip(boxes, read, strict=True) if text.strip()]


def load_engine(models_root: Path, spec: RecognizerSpec) -> PaddleEngine:
    """meikiocr's detector + ``spec``'s recogniser on CPU. Raises :class:`EngineLoadError`."""
    try:
        import onnxruntime  # noqa: F401
    except ImportError as exc:
        raise EngineLoadError(f"onnxruntime is not importable: {exc}") from exc
    try:
        det = open_session(models_root / DET_MODEL_NAME)
        rec = paddle_rec.load_recognizer(models_root / spec.model.filename, reverse=spec.reverse)
    except Exception as exc:  # noqa: BLE001 — onnxruntime raises its own unexported error types
        raise EngineLoadError(f"OCR models could not be loaded: {exc}") from exc
    logger.info("Video OCR engine loaded: models=%s recogniser=%s", models_root, spec.model.filename)
    return PaddleEngine(det, rec)

"""Japanese game-text OCR (detect + recognise) on onnxruntime, vendored from meikiocr.

Modified from rtr46/meikiocr@52aa607ecdc1038c7bf390f37cd9a8b306e8a08a, file
meikiocr/ocr.py, Apache License 2.0 (licenses/meikiocr/). Changes from upstream:

* PIL bilinear resize replaces ``cv2.resize``: no opencv dependency.
* Models load from an explicit directory (``model_installer``), never through
  huggingface_hub.
* Every detected box goes to the horizontal recogniser. Upstream sends boxes
  taller than wide to a vertical model, which loses one-glyph lines like 「！」.
* The vertical recogniser, ``run_recognition`` and the punctuation factor are
  removed; results are typed (:class:`OcrBox`).
* A lock serialises calls, so the region dialog and a running scan can share one
  engine.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from anki_miner.services.video_ocr.errors import EngineLoadError
from anki_miner.services.video_ocr.model_installer import DET_MODEL_NAME, REC_MODEL_NAME

logger = logging.getLogger(__name__)

_DET_W, _DET_H = 960, 544
_REC_W, _REC_H = 960, 32
_DET_THRESHOLD = 0.5
_REC_THRESHOLD = 0.1
_X_OVERLAP = 0.3
_EPS = 1e-6
_MAX_BATCH = 8
_SWAPPED_PAIRS = {
    "儡傀": "傀儡",
    "談冗": "冗談",
    "汰淘": "淘汰",
    "沱滂": "滂沱",
    "攣痙": "痙攣",
    "酊酩": "酩酊",
    "麭麺": "麺麭",
    "哭慟": "慟哭",
}

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class OcrBox:
    """One recognised text line, in the input image's pixel coordinates."""

    text: str
    bbox: Box  # x1, y1, x2, y2
    mean_conf: float


def _resize(image: np.ndarray, width: int, height: int) -> np.ndarray:
    # A resize is channel-order agnostic, so BGR passes through PIL unchanged.
    return np.asarray(Image.fromarray(np.ascontiguousarray(image)).resize((width, height), Image.Resampling.BILINEAR))


def _overlaps(c1: int, c2: int, a1: int, a2: int) -> bool:
    if c1 >= a2 or a1 >= c2:
        return False
    inter = max(0, min(c2, a2) - max(c1, a1))
    return inter / min(c2 - c1 + _EPS, a2 - a1 + _EPS) > _X_OVERLAP


class MeikiEngine:
    """Detector + horizontal recogniser over two onnxruntime sessions."""

    def __init__(self, det_session: Any, rec_session: Any) -> None:
        self._det = det_session
        self._rec = rec_session
        self._lock = threading.Lock()

    def read_boxes(self, bgr: np.ndarray) -> list[OcrBox]:
        """Detect and read every horizontal text line in ``bgr`` (H x W x 3, uint8), top to bottom."""
        with self._lock:
            return self._recognise(bgr, self._detect(bgr))

    def _detect(self, image: np.ndarray) -> list[Box]:
        h, w = image.shape[:2]
        scale = min(_DET_W / w, _DET_H / h)
        rw, rh = max(1, int(w * scale)), max(1, int(h * scale))
        tensor = np.zeros((_DET_H, _DET_W, 3), dtype=np.float32)
        tensor[:rh, :rw] = _resize(image, rw, rh).astype(np.float32) / 255.0
        inputs = self._det.get_inputs()
        feeds = {
            inputs[0].name: tensor.transpose(2, 0, 1)[np.newaxis],
            inputs[1].name: np.array([[_DET_W / scale, _DET_H / scale]], dtype=np.int64),
        }
        _, boxes, scores = self._det.run(None, feeds)
        confident = boxes[0][scores[0] > _DET_THRESHOLD]
        if confident.shape[0] == 0:
            return []
        clipped = np.clip(confident, 0, np.array([w, h, w, h])).astype(np.int32)
        found = [(int(b[0]), int(b[1]), int(b[2]), int(b[3])) for b in clipped]
        return sorted((b for b in found if b[2] > b[0] and b[3] > b[1]), key=lambda b: (b[1], b[0]))

    def _run_rec(self, batch: np.ndarray) -> Any:
        sizes = np.array([[_REC_W, _REC_H]], dtype=np.int64)
        return self._rec.run(None, {"images": batch, "orig_target_sizes": sizes})

    def _recognise(self, image: np.ndarray, boxes: list[Box]) -> list[OcrBox]:
        tensors: list[np.ndarray] = []
        metas: list[tuple[Box, int]] = []
        for box in boxes:
            x1, y1, x2, y2 = box
            crop = image[y1:y2, x1:x2]
            h, w = crop.shape[:2]
            new_h, new_w = _REC_H, max(1, int(round(w * _REC_H / h)))
            if new_w > _REC_W:
                new_h = max(1, int(round(_REC_H * _REC_W / new_w)))
                new_w = _REC_W
            padded = np.zeros((_REC_H, _REC_W, 3), dtype=np.float32)
            padded[:new_h, :new_w] = _resize(crop, new_w, new_h).astype(np.float32) / 255.0
            tensors.append(padded.transpose(2, 0, 1))
            metas.append((box, new_w))
        results: list[OcrBox] = []
        for start in range(0, len(tensors), _MAX_BATCH):
            labels, char_boxes, scores = self._run_rec(np.stack(tensors[start : start + _MAX_BATCH]))
            for offset, (lbls, cboxes, scrs) in enumerate(zip(labels, char_boxes, scores, strict=True)):
                box, eff_w = metas[start + offset]
                results.append(self._decode(box, eff_w, lbls, cboxes, scrs))
        return [r for r in results if r.text]

    @staticmethod
    def _decode(box: Box, eff_w: int, labels: Any, char_boxes: Any, scores: Any) -> OcrBox:
        x1, _, x2, _ = box
        crop_w = x2 - x1
        candidates: list[tuple[float, int, int, str]] = []
        for label, cbox, score in zip(labels, char_boxes, scores, strict=True):
            if score < _REC_THRESHOLD or float(cbox[0]) >= eff_w:
                continue
            rx1, rx2 = float(cbox[0]), min(float(cbox[2]), float(eff_w))
            candidates.append(
                (float(score), x1 + int(rx1 / eff_w * crop_w), x1 + int(rx2 / eff_w * crop_w), chr(int(label)))
            )
        candidates.sort(key=lambda c: c[0], reverse=True)
        accepted: list[tuple[float, int, int, str]] = []
        for cand in candidates:
            if not any(_overlaps(cand[1], cand[2], a[1], a[2]) for a in accepted):
                accepted.append(cand)
        accepted.sort(key=lambda c: c[1])
        text = "".join(c[3] for c in accepted)
        for wrong, right in _SWAPPED_PAIRS.items():
            text = text.replace(wrong, right)
        mean = sum(c[0] for c in accepted) / len(accepted) if accepted else 0.0
        return OcrBox(text=text, bbox=box, mean_conf=mean)


def load_engine(models_root: Path) -> MeikiEngine:
    """Open both pinned models on CPU. Raises :class:`EngineLoadError`."""
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise EngineLoadError(f"onnxruntime is not importable: {exc}") from exc
    ort.set_default_logger_severity(3)
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    providers = ["CPUExecutionProvider"]
    try:
        det = ort.InferenceSession(str(models_root / DET_MODEL_NAME), sess_options=options, providers=providers)
        rec = ort.InferenceSession(str(models_root / REC_MODEL_NAME), sess_options=options, providers=providers)
    except Exception as exc:  # noqa: BLE001 — onnxruntime raises its own unexported error types
        raise EngineLoadError(f"OCR models could not be loaded: {exc}") from exc
    logger.info("Video OCR engine loaded: models=%s", models_root)
    return MeikiEngine(det, rec)

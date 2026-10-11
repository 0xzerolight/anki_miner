"""PP-OCR text-line recogniser (PaddleOCR models in RapidOCR's ONNX conversion) on onnxruntime.

Standalone: imports the standard library, numpy, PIL and (lazily) onnxruntime, never
``anki_miner``. The app loads it for Video OCR; the managed mokuro environment loads
it through ``mokuro_driver.py`` for Manga OCR.

Pre/post-processing follow RapidOCR v3.10.0 ``ch_ppocr_rec`` (Apache-2.0): BGR in,
height 48, width from the batch's widest aspect ratio (at least 320/48), scaled to
[-1, 1] and zero-padded on the right; greedy CTC over ``[blank, *character, " "]``,
``character`` being the list RapidOCR stores in the model's metadata.
``_visual_to_logical`` is modelled on PaddleOCR's ``BaseRecLabelDecode.pred_reverse``
(Apache-2.0), for the Arabic-script models, which emit text in visual order.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_HEIGHT = 48
_MIN_RATIO = 320 / 48
_MAX_BATCH = 8
# Runs that keep their order inside a reversed right-to-left line start at a character
# of bidi class L, EN or AN: left-to-right letters (accented Latin, Cyrillic), ASCII,
# Arabic-Indic and Persian digits, and the Arabic separators ٫ ٬. A number sign of
# class ET (% ٪ $ # °) next to a European or Persian digit (EN) joins its number, as
# UAX #9 rule W5 has it; beside Arabic-Indic digits (AN) it stays neutral. Any other
# non-RTL character (space, comma, period, a lone %) stays in an open run when the
# next character belongs to a run ("1,000", "hello world"), and a wider gap only
# between two left-to-right letters ("hello, world"): rule N1 treats numbers as
# right-to-left, so "10 - 5" is two runs. The space before a right-to-left word stays
# a word gap.
_RUN_CLASSES = frozenset({"L", "EN", "AN"})
_RTL_CLASSES = frozenset({"R", "AL", "NSM"})
# A right-to-left line draws each bracket mirrored, so the glyph read as "(" is a logical ")".
_MIRROR = str.maketrans("()[]{}<>«»", ")(][}{><»«")


def _visual_to_logical(text: str) -> str:
    kinds = [unicodedata.bidirectional(ch) for ch in text]
    # W5: a sequence of ETs touching an EN digit on either side becomes part of it.
    for i in range(1, len(kinds)):
        if kinds[i] == "ET" and kinds[i - 1] == "EN":
            kinds[i] = "EN"
    for i in range(len(kinds) - 2, -1, -1):
        if kinds[i] == "ET" and kinds[i + 1] == "EN":
            kinds[i] = "EN"
    # next_strong[i]: the class of the first run or RTL character at or after i.
    next_strong = [""] * (len(kinds) + 1)
    for i in range(len(kinds) - 1, -1, -1):
        strong = kinds[i] in _RUN_CLASSES or kinds[i] in _RTL_CLASSES
        next_strong[i] = kinds[i] if strong else next_strong[i + 1]
    parts: list[str] = []
    run = ""
    run_strong = ""  # the class of the run's last L, EN or AN character
    for i, ch in enumerate(text):
        kind = kinds[i]
        if kind in _RUN_CLASSES:
            run += ch
            run_strong = kind
            continue
        if run and kind not in _RTL_CLASSES:
            next_kind = kinds[i + 1] if i + 1 < len(kinds) else ""
            if next_kind in _RUN_CLASSES or (run_strong == "L" and next_strong[i + 1] == "L"):
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

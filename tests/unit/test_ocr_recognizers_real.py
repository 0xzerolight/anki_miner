"""Opt-in: every pinned recogniser reads one rendered line of its script (ANKI_MINER_TEST_PPOCR_MODELS=<dir>)."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont
from rapidfuzz.distance import Levenshtein

from anki_miner.services.ocr.paddle_rec import load_recognizer
from anki_miner.services.ocr.recognizers import RECOGNIZERS

pytestmark = pytest.mark.asr  # numpy-backed (the gate in test_asr_marker_gating.py)
_ROOT = os.environ.get("ANKI_MINER_TEST_PPOCR_MODELS")
_FONTS = Path(__file__).resolve().parents[2] / "anki_miner" / "gui" / "resources" / "fonts"
# key -> (line, font path); system fonts are skipped when absent
_LINES = {
    "korean": ("오늘은 날씨가 좋네요", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    "eslav": ("Где находится станция?", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    "el": ("Πού είναι ο σταθμός;", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    "th": ("วันนี้อากาศดีมาก", str(_FONTS / "NotoSansThai-Regular.ttf")),
    "arabic": ("أين محطة القطار", str(_FONTS / "NotoNaskhArabic-Regular.ttf")),
    "ppocrv6-small": ("¿Dónde está la estación?", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
}


@pytest.mark.skipif(not _ROOT, reason="set ANKI_MINER_TEST_PPOCR_MODELS to a folder with the pinned recognisers")
@pytest.mark.parametrize("key", sorted(set(_LINES) & set(RECOGNIZERS)))
def test_recogniser_reads_its_script(key):
    text, font_path = _LINES[key]
    spec = RECOGNIZERS[key]
    model = Path(_ROOT) / spec.model.filename
    if not model.is_file():
        pytest.skip(f"model missing: {model}")
    if not Path(font_path).is_file():
        pytest.skip(f"font missing: {font_path}")
    font = ImageFont.truetype(font_path, 40)
    image = Image.new("RGB", (900, 70), "white")
    ImageDraw.Draw(image).text((10, 8), text, font=font, fill="black")
    rec = load_recognizer(model, reverse=spec.reverse)
    read = rec.recognize([np.asarray(image)[:, :, ::-1].copy()])[0][0]
    assert Levenshtein.distance(read, text) / len(text) <= 0.10, read

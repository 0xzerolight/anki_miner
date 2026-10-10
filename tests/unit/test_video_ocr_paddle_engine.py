"""meikiocr's detector + a PP-OCR recogniser, and the runtime that picks it per language."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from anki_miner.services.video_ocr import paddle_engine, runtime
from anki_miner.services.video_ocr.meiki_engine import OcrBox
from anki_miner.services.video_ocr.paddle_engine import PaddleEngine
from tests.unit._ocr_profiles import with_recognizer
from tests.unit.test_video_ocr_engine import _FakeDet

pytestmark = pytest.mark.asr  # numpy-backed (the gate in test_asr_marker_gating.py)


def _box(text, x1):
    return OcrBox(text, (x1, 0, x1 + 80, 30), 0.9)


class _FakeRecognizer:
    def __init__(self, texts):
        self.texts, self.crops = list(texts), []

    def recognize(self, images):
        self.crops.extend(images)
        out, self.texts = self.texts[: len(images)], self.texts[len(images) :]
        return [(t, 0.9) for t in out]


def test_each_detected_box_is_cropped_padded_and_read_top_to_bottom():
    det = _FakeDet([[10, 50, 210, 80], [10, 10, 110, 40]], [0.9, 0.9])
    rec = _FakeRecognizer(["first", "second"])
    boxes = PaddleEngine(det, rec).read_boxes(np.zeros((120, 400, 3), dtype=np.uint8))
    assert [b.text for b in boxes] == ["first", "second"]
    assert boxes[0].bbox == (10, 10, 110, 40)  # reported box is the detector's, unpadded
    assert rec.crops[0].shape[0] > 30  # the crop the recogniser saw was padded vertically


def test_blank_reads_are_dropped():
    det = _FakeDet([[10, 10, 110, 40]], [0.9])
    assert PaddleEngine(det, _FakeRecognizer(["  "])).read_boxes(np.zeros((60, 200, 3), dtype=np.uint8)) == []


def test_reader_follows_a_language_switch(monkeypatch, test_config):
    """Review Focus 4: a cached Japanese engine never reads the next language's frames."""
    loaded = []
    monkeypatch.setattr(runtime.onnx_pack_installer, "ensure_on_syspath", lambda root: None)
    monkeypatch.setattr(
        "anki_miner.services.video_ocr.meiki_engine.load_engine", lambda root: loaded.append("meiki") or object()
    )
    monkeypatch.setattr(paddle_engine, "load_engine", lambda root, spec: loaded.append(spec.model.filename) or object())
    with_recognizer(monkeypatch, runtime, "ko", "korean")
    runtime._clear_cache()
    runtime.get_reader(replace(test_config, language="ja"))
    runtime.get_reader(replace(test_config, language="ja"))
    runtime.get_reader(replace(test_config, language="ko"))
    runtime.get_reader(replace(test_config, language="ja"))
    assert loaded == ["meiki", "korean_PP-OCRv5_rec_mobile.onnx", "meiki"]
    runtime._clear_cache()


def test_a_spaced_rtl_language_joins_right_to_left_with_spaces(monkeypatch, test_config):
    """Review Focus 3, video side: the reader hands join_rows the language's separator and direction."""
    engine = SimpleNamespace(read_boxes=lambda bgr: [_box("عالم", 0), _box("مرحبا", 100)])
    monkeypatch.setattr(runtime, "_engine", lambda *a: engine)
    with_recognizer(monkeypatch, runtime, "ar", "arabic")
    assert runtime.get_reader(replace(test_config, language="ar")).read_text(None) == "مرحبا عالم"


def test_japanese_keeps_its_cleanup(monkeypatch, test_config):
    """Japanese (no recogniser) still strips the speaker name and restores ！, which join_rows would keep raw."""
    engine = SimpleNamespace(read_boxes=lambda bgr: [_box("ルカ：行くぞ!", 0)])
    monkeypatch.setattr(runtime, "_engine", lambda *a: engine)
    assert runtime.get_reader(replace(test_config, language="ja")).read_text(None) == "行くぞ！"


def test_required_models_are_meikis_pair_for_japanese_and_detector_plus_recogniser_otherwise():
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.ocr.model_installer import MEIKI_SPECS
    from anki_miner.services.ocr.recognizers import RECOGNIZERS

    assert runtime.required_models(get_profile("ja")) == MEIKI_SPECS
    ko = replace(get_profile("ko"), ocr_recognizer="korean")
    assert runtime.required_models(ko) == (MEIKI_SPECS[0], RECOGNIZERS["korean"].model)

"""The vendored meikiocr engine: pre/post-processing with fake sessions, plus an opt-in real-model golden."""

from __future__ import annotations

import os
import threading
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from anki_miner.services.video_ocr import meiki_engine
from anki_miner.services.video_ocr.meiki_engine import MeikiEngine, OcrBox


class _FakeDet:
    def __init__(self, boxes: list[list[float]], scores: list[float]) -> None:
        self.boxes = np.array([boxes], dtype=np.float32).reshape(1, -1, 4)
        self.scores = np.array([scores], dtype=np.float32).reshape(1, -1)
        self.feeds: dict = {}

    def get_inputs(self):
        return [SimpleNamespace(name="images"), SimpleNamespace(name="orig_target_sizes")]

    def run(self, _outputs, feeds):
        self.feeds = feeds
        return [np.zeros((1, self.boxes.shape[1])), self.boxes, self.scores]


class _FakeRec:
    """Returns the same character candidates (model space, 960x32) for every crop."""

    def __init__(self, chars: list[tuple[str, float, float, float]], delay: float = 0.0) -> None:
        self.chars = chars
        self.delay = delay
        self.batches: list[np.ndarray] = []
        self.active = 0
        self.max_active = 0

    def run(self, _outputs, feeds):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        self.active -= 1
        batch = feeds["images"]
        self.batches.append(batch)
        n = batch.shape[0]
        labels = np.array([[ord(c) for c, *_ in self.chars]] * n, dtype=np.int64)
        boxes = np.array([[[x1, 0, x2, 32] for _, x1, x2, _ in self.chars]] * n, dtype=np.float32)
        scores = np.array([[s for *_, s in self.chars]] * n, dtype=np.float32)
        return [labels, boxes, scores]


def _image() -> np.ndarray:
    return np.full((200, 600, 3), 128, dtype=np.uint8)


def test_detector_input_is_letterboxed_to_960x544():
    det = _FakeDet([[10, 10, 300, 40]], [0.9])
    MeikiEngine(det, _FakeRec([("あ", 0, 30, 0.9)])).read_boxes(_image())
    tensor = det.feeds["images"]
    assert tensor.shape == (1, 3, 544, 960)
    assert tensor.dtype == np.float32
    assert tensor[0, :, 400:, :].max() == 0.0  # padding below the 600x200 image scaled by 1.6


def test_a_tall_one_glyph_box_is_read_by_the_horizontal_recogniser():
    det = _FakeDet([[100, 10, 130, 70]], [0.9])  # h > w: upstream would route it to the vertical model
    rec = _FakeRec([("！", 0, 16, 0.95)])
    assert [b.text for b in MeikiEngine(det, rec).read_boxes(_image())] == ["！"]
    assert rec.batches[0].shape[1:] == (3, 32, 960)


def test_low_confidence_boxes_and_chars_are_dropped():
    det = _FakeDet([[10, 10, 300, 40], [10, 100, 300, 130]], [0.9, 0.2])
    rec = _FakeRec([("猫", 0, 30, 0.9), ("犬", 40, 70, 0.05)])
    assert [b.text for b in MeikiEngine(det, rec).read_boxes(_image())] == ["猫"]


def test_overlapping_candidates_keep_the_most_confident_and_read_left_to_right():
    det = _FakeDet([[0, 0, 300, 30]], [0.9])
    rec = _FakeRec([("い", 40, 70, 0.8), ("あ", 0, 30, 0.6), ("ア", 2, 30, 0.9)])
    box = MeikiEngine(det, rec).read_boxes(_image())[0]
    assert box.text == "アい"
    assert box.mean_conf == pytest.approx(0.85)


def test_known_swapped_pairs_are_put_back():
    det = _FakeDet([[0, 0, 300, 30]], [0.9])
    rec = _FakeRec([("儡", 0, 30, 0.9), ("傀", 30, 60, 0.9)])
    assert MeikiEngine(det, rec).read_boxes(_image())[0].text == "傀儡"


def test_boxes_come_back_top_to_bottom_in_image_coordinates():
    det = _FakeDet([[10, 120, 300, 150], [10, 10, 300, 40]], [0.9, 0.9])
    boxes = MeikiEngine(det, _FakeRec([("a", 0, 30, 0.9)])).read_boxes(_image())
    assert [b.bbox for b in boxes] == [(10, 10, 300, 40), (10, 120, 300, 150)]


def test_concurrent_reads_are_serialised():
    rec = _FakeRec([("a", 0, 30, 0.9)], delay=0.05)
    engine = MeikiEngine(_FakeDet([[0, 0, 300, 30]], [0.9]), rec)
    threads = [threading.Thread(target=engine.read_boxes, args=(_image(),)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert rec.max_active == 1


def test_load_engine_reports_a_missing_runtime(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def _no_ort(name, *args, **kwargs):
        if name == "onnxruntime":
            raise ImportError("no onnxruntime")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_ort)
    with pytest.raises(meiki_engine.EngineLoadError, match="onnxruntime"):
        meiki_engine.load_engine(tmp_path)


_MODELS = os.environ.get("ANKI_MINER_TEST_OCR_MODELS")


@pytest.mark.skipif(not _MODELS, reason="set ANKI_MINER_TEST_OCR_MODELS to a dir holding the two pinned models")
@pytest.mark.parametrize("text", ["こんにちは世界", "「行くぞ！」", "！"])
def test_real_models_read_a_rendered_line(text):
    from tests.unit._video_ocr_clips import render_line

    engine = meiki_engine.load_engine(Path(str(_MODELS)))
    boxes = engine.read_boxes(render_line(text))
    # The recogniser's label set is NFKC-folded: it reads "！" as "!" (upstream does the same).
    assert unicodedata.normalize("NFKC", "".join(b.text for b in boxes)) == unicodedata.normalize("NFKC", text)
    assert all(isinstance(b, OcrBox) for b in boxes)

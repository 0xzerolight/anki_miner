"""PP-OCR line recogniser: preprocessing, CTC decode and RTL reorder against a fake session."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from anki_miner.services.ocr import paddle_rec
from anki_miner.services.ocr.paddle_rec import PaddleRecognizer, _visual_to_logical

pytestmark = pytest.mark.asr  # numpy-backed (the gate in test_asr_marker_gating.py)

# Class ids: 0 blank, 1 "a", 2 "b", 3 "c", 4 " " (the appended space)
_BLANK, _A, _B, _C, _SPACE = range(5)


class _FakeSession:
    """An onnxruntime session over a three-character model; each image reads a fixed id sequence."""

    def __init__(self, steps: list[list[int]], characters: str = "a\nb\nc") -> None:
        self._steps = list(steps)
        self._characters = characters
        self.batches: list[np.ndarray] = []

    def get_modelmeta(self):
        return SimpleNamespace(custom_metadata_map={"character": self._characters})

    def get_inputs(self):
        return [SimpleNamespace(name="x")]

    def run(self, _outputs, feeds):
        batch = feeds["x"]
        self.batches.append(batch)
        mine, self._steps = self._steps[: batch.shape[0]], self._steps[batch.shape[0] :]
        width = max(len(s) for s in mine)
        probs = np.zeros((batch.shape[0], width, 5), dtype=np.float32)
        for row, steps in enumerate(mine):
            for t in range(width):
                probs[row, t, steps[t] if t < len(steps) else _BLANK] = 0.9
        return [probs]


def _line(h: int = 20, w: int = 100) -> np.ndarray:
    return np.full((h, w, 3), 255, dtype=np.uint8)


def test_ctc_decode_collapses_repeats_drops_blanks_and_keeps_spaces():
    rec = PaddleRecognizer(_FakeSession([[_A, _A, _BLANK, _A, _B, _SPACE, _C]]))
    assert rec.recognize([_line()]) == [("aab c", pytest.approx(0.9))]


def test_input_is_48_high_scaled_to_unit_range_and_padded_right():
    session = _FakeSession([[_A], [_A]])
    # The wide line (ratio 20) sets the batch width 48*20; the narrow one fills 48*2 and is padded.
    PaddleRecognizer(session).recognize([_line(h=20, w=400), _line(h=20, w=40)])
    batch = session.batches[0]
    assert batch.shape == (2, 3, 48, 960)
    assert batch.max() <= 1.0 and batch.min() >= -1.0
    assert np.all(batch[1, :, :, :96] == 1.0)  # white → +1
    assert np.all(batch[1, :, :, 96:] == 0.0)


def test_narrow_lines_still_get_the_320_minimum_width():
    session = _FakeSession([[_A]])
    PaddleRecognizer(session).recognize([_line(h=40, w=40)])
    assert session.batches[0].shape[-1] == 320


def test_batches_of_eight_keep_input_order():
    steps = [[_A]] * 8 + [[_B], [_C]]
    session = _FakeSession(steps)
    texts = [t for t, _ in PaddleRecognizer(session).recognize([_line()] * 10)]
    assert texts == ["a"] * 8 + ["b", "c"]
    assert [b.shape[0] for b in session.batches] == [8, 2]


def test_an_all_blank_line_reads_empty_with_zero_confidence():
    assert PaddleRecognizer(_FakeSession([[_BLANK, _BLANK]])).recognize([_line()]) == [("", 0.0)]


@pytest.mark.parametrize(
    ("visual", "logical"),
    [
        ("2026 ابحرم", "مرحبا 2026"),  # digits stay in reading order, the space stays a word gap
        ("ok ابحرم", "مرحبا ok"),
        ("۱۴۰۳ مالس", "سلام ۱۴۰۳"),  # Persian digits are an LTR run too
        ("(ابحرم)", "(مرحبا)"),  # brackets mirror, as the bidi algorithm draws them
        ("«مالس»", "«سلام»"),
        ("hello world ابحرم", "مرحبا hello world"),  # a space between two LTR words stays inside the run
        ("1,000 مالس", "سلام 1,000"),
        ("۱٫۵ مالس", "سلام ۱٫۵"),  # the Arabic decimal separator is a number character (AN)
        ("café ابحرم", "مرحبا café"),  # accented Latin is LTR too
    ],
)
def test_visual_to_logical_reverses_rtl_but_keeps_ltr_runs(visual, logical):
    assert _visual_to_logical(visual) == logical


def test_reverse_flag_applies_the_reorder():
    session = _FakeSession([[_A, _B, _C]], characters="ا\nب\nج")
    assert PaddleRecognizer(session, reverse=True).recognize([_line()])[0][0] == "جبا"
    assert PaddleRecognizer(_FakeSession([[_A, _B, _C]], characters="ا\nب\nج")).recognize([_line()])[0][0] == "ابج"


@pytest.mark.parametrize("name", ["paddle_rec.py", "mokuro_driver.py"])
def test_the_standalone_file_imports_nothing_from_anki_miner(name):
    tree = ast.parse((Path(paddle_rec.__file__).parent / name).read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names] + [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert not [m for m in modules if m.startswith("anki_miner")]
    # A relative import (from .paddle_rec import ...) has no package to resolve against in a script.
    assert not [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level > 0]

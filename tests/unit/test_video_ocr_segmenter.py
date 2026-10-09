"""segment(): the anchor-based diff gate, the OCR throttle and the merge rules, on synthetic samples."""

from __future__ import annotations

import random

import numpy as np
import pytest

from anki_miner.services.video_ocr import segmenter
from anki_miner.services.video_ocr.segmenter import MIN_CUE, Cue, segment

# numpy-backed (the gate in test_asr_marker_gating.py)
pytestmark = pytest.mark.asr

STEP = 0.1


def stream(values: list[int]) -> list[tuple[float, np.ndarray]]:
    return [(round(i * STEP, 6), np.full((40, 200, 3), v, dtype=np.uint8)) for i, v in enumerate(values)]


def reader(mapping: dict[int, str], calls: list[int] | None = None):
    def read(frame: np.ndarray) -> str:
        value = int(frame[0, 0, 0])
        if calls is not None:
            calls.append(value)
        return mapping.get(value, "")

    return read


def approx(cues: list[Cue]) -> list[tuple[float, float, str]]:
    return [(round(c.start, 2), round(c.end, 2), c.text) for c in cues]


def test_two_lines_separated_by_a_gap():
    values = [0] * 5 + [200] * 20 + [0] * 5 + [120] * 20
    cues = segment(stream(values), reader({200: "こんにちは", 120: "さようなら"}))
    assert approx(cues) == [(0.5, 2.5, "こんにちは"), (3.0, 5.0, "さようなら")]


def test_typewriter_reveal_becomes_one_cue_from_the_first_glyph():
    values = [50] * 5 + [100] * 5 + [150] * 5 + [200] * 20
    texts = {50: "こん", 100: "こんにち", 150: "こんにちは、", 200: "こんにちは、元気？"}
    assert approx(segment(stream(values), reader(texts))) == [(0.0, 3.5, "こんにちは、元気？")]


def test_a_half_drawn_last_glyph_still_merges():
    values = [100] * 5 + [200] * 20
    texts = {100: "こんにさ", 200: "こんにちは"}  # last glyph misread mid-reveal
    assert approx(segment(stream(values), reader(texts))) == [(0.0, 2.5, "こんにちは")]


def test_a_held_line_sharing_the_next_lines_start_stays_separate():
    values = [100] * 20 + [200] * 20
    texts = {100: "そうだな", 200: "そうだね、行こう"}
    assert approx(segment(stream(values), reader(texts))) == [(0.0, 2.0, "そうだな"), (2.0, 4.0, "そうだね、行こう")]


def test_a_held_line_over_a_changing_background_stays_separate():
    # The background flips every 0.6 s, so the held line arrives as short OCR groups of equal text.
    # Every pair of codes differs by more than PIXEL_DELTA, so each flip closes a span.
    values = ([100] * 6 + [40] * 6) * 2 + [200] * 20
    texts = {100: "そうだな", 40: "そうだな", 200: "そうだね、行こう"}
    assert approx(segment(stream(values), reader(texts))) == [(0.0, 2.4, "そうだな"), (2.4, 4.4, "そうだね、行こう")]


def test_equal_text_across_a_gap_stays_two_cues():
    values = [200] * 10 + [0] * 10 + [200] * 10
    assert len(segment(stream(values), reader({200: "はい"}))) == 2


def test_a_flicker_shorter_than_min_cue_is_dropped():
    values = [0] * 10 + [200] * 2 + [0] * 10
    assert segment(stream(values), reader({200: "x"})) == []


def test_a_slow_fade_out_closes_the_span_instead_of_swallowing_the_line():
    # 1 s at full brightness, then -10 per sample: each step is under PIXEL_DELTA, the drift is not.
    values = [250] * 10 + list(range(240, 0, -10))
    texts = dict.fromkeys(range(200, 256), "消える")  # OCR loses the text once it has faded below 200
    cues = segment(stream(values), reader(texts))
    assert [c.text for c in cues] == ["消える"]
    assert 1.0 <= cues[0].end <= 1.7


def test_busy_background_under_the_threshold_is_one_span():
    rng = np.random.default_rng(1)
    samples = []
    for i in range(30):
        frame = np.full((40, 200, 3), 200, dtype=np.uint8)
        noise = rng.random((40, 200)) < 0.01  # 1 % flips per sample: ~2 % differ from the anchor, under 3 %
        frame[noise] = 0
        frame[0, 0] = 200  # the fake reader's code pixel is never noise
        samples.append((round(i * STEP, 6), frame))
    calls: list[int] = []
    cues = segment(samples, reader({200: "台詞"}, calls))
    assert [c.text for c in cues] == ["台詞"]
    assert len(calls) == 1


def test_the_throttle_bounds_ocr_calls_during_constant_change():
    values = [100, 200] * 25  # 5 s of change on every sample
    calls: list[int] = []
    segment(stream(values), reader({}, calls))
    assert len(calls) <= 5 / segmenter.MIN_OCR_SPAN + 1


def test_the_gate_view_is_at_most_gate_width_wide():
    for width in (200, 241, 479, 719, 1280):
        view = segmenter._gate_view(np.zeros((10, width, 3), dtype=np.uint8))
        assert view.shape[1] <= segmenter.GATE_WIDTH, width


def test_progress_reports_every_sample_time():
    seen: list[float] = []
    segment(stream([0] * 7), reader({}), on_progress=seen.append)
    assert seen == [round(i * STEP, 6) for i in range(7)]


def test_invariants_hold_on_random_streams():
    for seed in range(200):
        rng = random.Random(seed)
        values: list[int] = []
        while len(values) < 300:
            values += [rng.choice([0, 100, 200])] * rng.randint(1, 30)
        total = len(values) * STEP
        cues = segment(stream(values), reader({100: "あ", 200: "い"}))
        for cue in cues:
            assert cue.text
            assert cue.end - cue.start >= MIN_CUE - 1e-9
            assert 0.0 <= cue.start < cue.end <= total + 1e-9
        for a, b in zip(cues, cues[1:], strict=False):
            assert a.end <= b.start + 1e-9, seed

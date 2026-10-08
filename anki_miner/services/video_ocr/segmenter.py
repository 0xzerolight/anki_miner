"""Turn a stream of region samples into timed cues. Pure: numpy only, no ffmpeg, no OCR engine.

The constants are initial values, tuned once on real longplay footage (plan Task 8).
They are not settings.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

import numpy as np

SAMPLE_FPS = 10
GATE_WIDTH = 240  # the gate compares a strided copy at most this wide
PIXEL_DELTA = 40  # grayscale change that counts a pixel as changed
CHANGED_FRACTION = 0.03  # share of changed pixels that closes a span
MIN_OCR_SPAN = 0.5  # shorter spans coalesce with the next: at most ~2 OCR calls per second
MIN_CUE = 0.3
TYPEWRITER_PIECE_MAX = 1.0  # prefix merges only follow a piece this short (a held line is a real line)

_LUMA_BGR = np.array([0.114, 0.587, 0.299], dtype=np.float32)


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass
class _Span:
    start: float
    end: float
    last: np.ndarray


def _gate_view(frame: np.ndarray) -> np.ndarray:
    step = max(1, -(-frame.shape[1] // GATE_WIDTH))
    return frame[::step, ::step, :3].astype(np.float32) @ _LUMA_BGR


def _changed(anchor: np.ndarray, view: np.ndarray) -> bool:
    if anchor.shape != view.shape:
        return True
    return float(np.mean(np.abs(view - anchor) > PIXEL_DELTA)) >= CHANGED_FRACTION


def _spans(
    samples: Iterable[tuple[float, np.ndarray]], step: float, on_progress: Callable[[float], None] | None
) -> Iterator[_Span]:
    span: _Span | None = None
    anchor: np.ndarray | None = None
    for t, frame in samples:
        if on_progress is not None:
            on_progress(t)
        view = _gate_view(frame)
        # Compared with the span's FIRST sample, not the previous one: a slow fade
        # changes little per step but drifts past the threshold, so it still closes.
        if span is not None and anchor is not None and not _changed(anchor, view):
            span.end = t + step
            span.last = frame
            continue
        if span is not None:
            yield span
        span, anchor = _Span(t, t + step, frame), view
    if span is not None:
        yield span


def _groups(spans: Iterable[_Span]) -> Iterator[_Span]:
    pending: _Span | None = None
    for span in spans:
        pending = span if pending is None else _Span(pending.start, span.end, span.last)
        if pending.end - pending.start >= MIN_OCR_SPAN - 1e-9:
            yield pending
            pending = None
    if pending is not None:
        yield pending


def _continues(previous: str, current: str) -> bool:
    """``current`` extends ``previous`` (typewriter), allowing a half-drawn last glyph."""
    return current.startswith(previous) or (len(previous) >= 2 and current.startswith(previous[:-1]))


def segment(
    samples: Iterable[tuple[float, np.ndarray]],
    read_text: Callable[[np.ndarray], str],
    *,
    on_progress: Callable[[float], None] | None = None,
    fps: int = SAMPLE_FPS,
) -> list[Cue]:
    """Cues for the dialogue in ``samples`` (``(t_seconds, BGR region)``, in order, ``1/fps`` apart)."""
    cues: list[Cue] = []
    current: Cue | None = None
    piece = 0.0  # how long current's latest text has been on screen
    for group in _groups(_spans(samples, 1.0 / fps, on_progress)):
        text = read_text(group.last)
        if not text:
            if current is not None:
                cues.append(current)
                current = None
            continue
        duration = group.end - group.start
        if current is not None and text == current.text:
            current = Cue(current.start, group.end, text)
            piece += duration
        elif current is not None and piece <= TYPEWRITER_PIECE_MAX + 1e-9 and _continues(current.text, text):
            current = Cue(current.start, group.end, text)
            piece = duration
        else:
            if current is not None:
                cues.append(current)
            current = Cue(group.start, group.end, text)
            piece = duration
    if current is not None:
        cues.append(current)
    return [c for c in cues if c.end - c.start >= MIN_CUE - 1e-9]

"""One video in, one timed .srt out: frame source → OCR → cleanup → segmenter → srt_writer."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Protocol

import numpy as np

from anki_miner.exceptions import OperationCancelled
from anki_miner.services.video_ocr import runtime
from anki_miner.services.video_ocr.frame_source import FrameSourceError, Region, iter_samples
from anki_miner.services.video_ocr.meiki_engine import OcrBox
from anki_miner.services.video_ocr.segmenter import SAMPLE_FPS, segment
from anki_miner.services.video_ocr.text_cleanup import clean
from anki_miner.utils.audio_track_detector import get_media_duration_seconds
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)


class VideoOcrStatus(Enum):
    """Outcome of :func:`scan_video` (mapped to a ``tr()`` message by the worker)."""

    SUCCESS = auto()
    #: The scan finished and found no dialogue; no file is written.
    NO_TEXT = auto()
    #: ffmpeg could not decode the video before the first sample.
    DECODE_FAILED = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class VideoOcrResult:
    status: VideoOcrStatus
    out_srt: Path | None = None
    cue_count: int = 0
    detail: str = ""


class TextReader(Protocol):
    def read_boxes(self, bgr: np.ndarray) -> list[OcrBox]: ...


def _reader(config, reader: TextReader | None) -> TextReader:
    return reader if reader is not None else runtime.get_engine(config.onnx_pack_root, config.video_ocr_models_root)


def read_region_text(config, crop: np.ndarray) -> str:
    """The cleaned text the scan would read from ``crop`` ("Test this frame")."""
    return clean(_reader(config, None).read_boxes(crop))


def scan_video(
    config,
    video: Path,
    region: Region,
    out_srt: Path,
    *,
    progress_cb: Callable[[float, float | None], None] | None = None,
    cancel_event: threading.Event | None = None,
    reader: TextReader | None = None,
) -> VideoOcrResult:
    """Read ``region`` of ``video`` into ``out_srt``. Raises only ``EngineLoadError`` (fatal for a queue)."""
    started = time.monotonic()
    duration = get_media_duration_seconds(video, resolve_ffprobe(config))
    text_reader = _reader(config, reader)

    def _on_sample(t: float) -> None:
        if progress_cb is not None:
            progress_cb(t, duration)

    try:
        samples = iter_samples(config, video, region, fps=SAMPLE_FPS, cancel_event=cancel_event)
        cues = segment(samples, lambda frame: clean(text_reader.read_boxes(frame)), on_progress=_on_sample)
    except OperationCancelled:
        return VideoOcrResult(VideoOcrStatus.CANCELLED)
    except FrameSourceError as exc:
        logger.warning("Video OCR: %s could not be decoded: %s", video.name, exc)
        return VideoOcrResult(VideoOcrStatus.DECODE_FAILED, detail=str(exc))
    if cancel_event is not None and cancel_event.is_set():
        return VideoOcrResult(VideoOcrStatus.CANCELLED)
    if not cues:
        return VideoOcrResult(VideoOcrStatus.NO_TEXT)
    from anki_miner.services.asr import srt_writer

    srt_writer.segments_to_srt([(c.start, c.end, c.text) for c in cues], out_srt)
    log_summary(
        logger,
        "Video OCR done",
        file=video,
        cues=len(cues),
        seconds=f"{time.monotonic() - started:.1f}",
        video_seconds=f"{duration:.0f}" if duration else "?",
    )
    return VideoOcrResult(VideoOcrStatus.SUCCESS, out_srt=out_srt, cue_count=len(cues))

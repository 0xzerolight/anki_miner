"""ffmpeg frame access for Video OCR: full stills for the region dialog, cropped samples for the scan.

Both go through ffmpeg's default autorotation, so a region drawn on a still maps
onto exactly the pixels the scan crops.
"""

from __future__ import annotations

import io
import logging
import subprocess
import threading
from collections import deque
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from PIL import Image

from anki_miner.exceptions import OperationCancelled
from anki_miner.services.video_ocr.errors import FrameSourceError
from anki_miner.services.video_ocr.region import MAX_SAMPLE_WIDTH, MIN_REGION_PX, Region, crop_box, sample_size
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg
from anki_miner.utils.subprocess_log import log_command
from anki_miner.utils.subprocess_utils import no_window_kwargs

__all__ = [
    "MAX_SAMPLE_WIDTH",
    "MIN_REGION_PX",
    "FrameSourceError",
    "Region",
    "crop_box",
    "crop_frame",
    "iter_samples",
    "sample_size",
    "still_at",
]

logger = logging.getLogger(__name__)

_STILL_TIMEOUT_S = 60


def crop_frame(frame: np.ndarray, region: Region) -> np.ndarray:
    """``region`` of a full BGR frame, exactly as the scan samples it (same crop, same cap)."""
    x, y, w, h = crop_box(region, (frame.shape[1], frame.shape[0]))
    crop = np.ascontiguousarray(frame[y : y + h, x : x + w])
    out_w, out_h = sample_size(w, h)
    if (out_w, out_h) == (w, h):
        return crop
    return np.asarray(Image.fromarray(crop).resize((out_w, out_h), Image.Resampling.BICUBIC))


def still_at(config, video: Path, t: float) -> np.ndarray:
    """The displayed frame at ``t`` seconds as a BGR array. Raises :class:`FrameSourceError`."""
    cmd = [
        resolve_ffmpeg(config),
        "-nostdin",
        "-v",
        "error",
        "-ss",
        f"{max(0.0, t):.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-c:v",
        "png",
        "pipe:1",
    ]
    log_command(logger, "Video OCR still", cmd)
    try:
        done = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_STILL_TIMEOUT_S,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FrameSourceError(f"ffmpeg could not read a frame: {exc}") from exc
    if done.returncode != 0 or not done.stdout:
        tail = done.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise FrameSourceError(f"ffmpeg could not read a frame at {t:.1f}s: {tail}")
    with Image.open(io.BytesIO(done.stdout)) as image:
        rgb = np.asarray(image.convert("RGB"))
    return np.ascontiguousarray(rgb[:, :, ::-1])


def iter_samples(
    config,
    video: Path,
    region: Region,
    *,
    fps: int,
    cancel_event: threading.Event | None = None,
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield ``(t_seconds, BGR region)`` every ``1/fps`` seconds, timed from the FILE start.

    ffmpeg's default constant-frame-rate output for rawvideo pads a video stream
    that starts after the file does (audio from 0 s, video from 1 s), so sample k
    is always ``k / fps`` of file time; adding the stream's start offset would
    count it twice. Raises ``OperationCancelled`` when ``cancel_event`` stopped
    the decode, and :class:`FrameSourceError` when ffmpeg failed before the first
    sample. A failure after some samples (a damaged tail) only logs.
    """
    first = still_at(config, video, 0.0)
    x, y, w, h = crop_box(region, (first.shape[1], first.shape[0]))
    out_w, out_h = sample_size(w, h)
    cmd = [
        resolve_ffmpeg(config),
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(video),
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"crop={w}:{h}:{x}:{y}:exact=1,fps={fps},scale={out_w}:{out_h},format=bgr24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    log_command(logger, "Video OCR scan", cmd)
    frame_bytes = out_w * out_h * 3
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs()
    )
    assert proc.stdout is not None and proc.stderr is not None
    stdout, stderr = proc.stdout, proc.stderr
    stderr_tail: deque[str] = deque(maxlen=20)
    finished = threading.Event()

    def _drain() -> None:
        for raw in stderr:
            stderr_tail.append(raw.decode("utf-8", errors="replace").rstrip())

    def _watch(event: threading.Event) -> None:
        while not finished.is_set():
            if event.wait(0.05):
                proc.kill()
                return

    threads = [threading.Thread(target=_drain, daemon=True)]
    if cancel_event is not None:
        threads.append(threading.Thread(target=_watch, args=(cancel_event,), daemon=True))
    for thread in threads:
        thread.start()
    count = 0
    try:
        while True:
            buf = stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield count / fps, np.frombuffer(buf, dtype=np.uint8).reshape(out_h, out_w, 3)
            count += 1
        returncode = proc.wait()
    finally:
        finished.set()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for thread in threads:
            thread.join(timeout=2)
        stdout.close()
        stderr.close()
    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled("Video OCR scan cancelled")
    if returncode != 0:
        detail = " | ".join(stderr_tail)
        if count == 0:
            raise FrameSourceError(f"ffmpeg exited {returncode}: {detail}")
        logger.warning("Video OCR scan: ffmpeg exited %d after %d samples: %s", returncode, count, detail)

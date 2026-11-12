"""Image subtitle tracks (PGS, VobSub, DVB) as a Video OCR source (F-18, step 1).

ffmpeg draws the chosen image track onto a plain canvas the size of the track,
and the scan reads that picture exactly as it reads burned-in text: the frame
sampler, OCR, segmenter and .srt writer do not change. Verified with ffmpeg 8
at planning time: ``-copyts`` keeps each cue at its own time (a .sup whose
first cue is at 1 s is otherwise shifted to 0 s), and ``-t`` is required
because the colour canvas never ends on its own.

The canvas is the size the track is authored for, and ffmpeg's own subtitle
renderer is given the same size (``-canvas_size``). Left to itself, ffmpeg
sizes a track it could not size from the video, and a cropped encode (1920x800
under a 1920x1080 PGS track) squashes the subtitles into that smaller canvas.

numpy-free: the Video OCR tab imports it at app startup.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from anki_miner.exceptions import OperationCancelled
from anki_miner.services.video_ocr.errors import FrameSourceError
from anki_miner.utils.audio_track_detector import get_video_size, list_subtitle_streams
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised

logger = logging.getLogger(__name__)

#: Files that hold nothing but an image subtitle track: Blu-ray PGS, and either
#: half of a DVD VobSub pair (ffmpeg opens the .idx and reads the .sub beside it).
BITMAP_FILE_SUFFIXES = frozenset({".sup", ".idx", ".sub"})

#: The canvas colour (ruling F18-1). Mid-grey, not black: white PGS text with a
#: dark outline and the dark text some VobSub palettes produce both stay
#: legible on it, so no source needs inverting.
CANVAS_COLOUR = "0x5a5a5a"

#: Seconds drawn past the track's last packet, so its last cue shows and clears.
END_MARGIN_S = 1.0

#: Canvas when neither the track nor a video names a size: the Blu-ray frame PGS is authored for.
_FALLBACK_SIZE = (1920, 1080)

#: Listing a long film's subtitle packets reads the whole file once.
_PACKET_PROBE_TIMEOUT_S = 300

#: The segment that opens every PGS display set; its payload starts with the frame width and height.
_PGS_COMPOSITION_SEGMENT = 0x16

#: One row of ffprobe's ``-show_data`` hexdump: offset, then up to eight groups of hex digits.
_HEX_ROW = re.compile(r"[0-9a-f]{8}: ([0-9a-f]{2,4}(?: [0-9a-f]{2,4})*)")


@dataclass(frozen=True)
class BitmapSource:
    """What ffmpeg opens, which subtitle track it draws, and the canvas size and length."""

    path: Path
    sub_index: int
    width: int
    height: int
    end_seconds: float
    #: The container's ``format.start_time``: a Blu-ray .m2ts/.ts starts above 0 s.
    #: Cues are moved back by it onto the file-start clock that mining's ``-ss``
    #: and the picture path use. Always 0 for a bare .sup or VobSub pair.
    start_seconds: float = 0.0


def is_bitmap_file(path: Path) -> bool:
    """Whether *path* is an image subtitle file (``.sup``, ``.idx``, ``.sub``) rather than a video."""
    return path.suffix.lower() in BITMAP_FILE_SUFFIXES


def vobsub_index(path: Path) -> Path | None:
    """The ``.idx`` of a VobSub pair given either half, or None when the other half is missing."""
    upper = path.suffix.isupper()
    idx = path.with_suffix(".IDX" if upper else ".idx")
    sub = path.with_suffix(".SUB" if upper else ".sub")
    return idx if idx.is_file() and sub.is_file() else None


def track_end_seconds(
    path: Path, sub_index: int, ffprobe_cmd: str, cancel_event: threading.Event | None = None
) -> float | None:
    """When subtitle track *sub_index* of *path* ends: its last packet's time plus duration; None on failure.

    Raises ``OperationCancelled`` when *cancel_event* stopped the listing.
    """
    cmd = [
        ffprobe_cmd,
        "-v",
        "error",
        "-select_streams",
        f"s:{sub_index}",
        "-show_entries",
        "packet=pts_time,duration_time",
        "-of",
        "csv=p=0",
        str(path),
    ]
    done = run_supervised(cmd, timeout_s=_PACKET_PROBE_TIMEOUT_S, cancel=cancel_event, op="ffprobe subtitle-packets")
    if done.state is SupervisedState.CANCELLED:
        raise OperationCancelled("Video OCR scan cancelled")
    if done.state is not SupervisedState.COMPLETED:
        logger.warning(
            "Video OCR: could not list the subtitle packets of %s: %s",
            path.name,
            done.error or done.state.value,
        )
        return None
    if done.returncode != 0:
        logger.warning(
            "Video OCR: ffprobe exited %d listing the subtitle packets of %s: %s",
            done.returncode,
            path.name,
            (done.stderr or "").strip()[-300:],
        )
        return None
    end: float | None = None
    for line in (done.stdout or "").splitlines():
        fields = [field.strip() for field in line.split(",")]
        try:
            pts = float(fields[0])
        except ValueError:
            continue
        try:
            duration = float(fields[1]) if len(fields) > 1 else 0.0
        except ValueError:  # "N/A": PGS clears a cue with a packet of its own
            duration = 0.0
        end = pts + duration if end is None else max(end, pts + duration)
    return end


def pgs_composition_size(
    path: Path, sub_index: int, ffprobe_cmd: str, cancel_event: threading.Event | None = None
) -> tuple[int, int] | None:
    """The frame size PGS track *sub_index* of *path* is authored for, read from its first packet; None on failure.

    ffprobe sizes a PGS track only by decoding a cue within its probe window
    (5 s / 5 MB of the file), and a film's first line usually comes later.
    Raises ``OperationCancelled`` when *cancel_event* stopped the read.
    """
    cmd = [
        ffprobe_cmd,
        "-v",
        "error",
        "-select_streams",
        f"s:{sub_index}",
        "-read_intervals",
        "%+#1",
        "-show_entries",
        "packet=data",
        "-show_data",
        "-of",
        "default=nw=1:nk=1",
        str(path),
    ]
    done = run_supervised(cmd, timeout_s=_PACKET_PROBE_TIMEOUT_S, cancel=cancel_event, op="ffprobe subtitle-size")
    if done.state is SupervisedState.CANCELLED:
        raise OperationCancelled("Video OCR scan cancelled")
    if done.state is not SupervisedState.COMPLETED or done.returncode != 0:
        logger.warning(
            "Video OCR: could not read the first subtitle packet of %s: %s",
            path.name,
            done.error or (done.stderr or "").strip()[-300:] or done.state.value,
        )
        return None
    rows = (_HEX_ROW.match(line) for line in (done.stdout or "").splitlines())
    data = bytes.fromhex("".join(row.group(1).replace(" ", "") for row in rows if row))
    # Segments: a type byte, a 2-byte payload length, the payload.
    pos = 0
    while pos + 3 <= len(data):
        length = int.from_bytes(data[pos + 1 : pos + 3], "big")
        payload = data[pos + 3 : pos + 3 + length]
        if data[pos] == _PGS_COMPOSITION_SEGMENT and len(payload) >= 4:
            width, height = int.from_bytes(payload[0:2], "big"), int.from_bytes(payload[2:4], "big")
            return (width, height) if width and height else None
        pos += 3 + length
    return None


def container_start_seconds(path: Path, ffprobe_cmd: str) -> float:
    """*path*'s ``format.start_time`` in seconds; 0 when it is unset or cannot be read."""
    cmd = [ffprobe_cmd, "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", str(path)]
    done = run_supervised(cmd, timeout_s=30, op="ffprobe start-time")
    if done.state is not SupervisedState.COMPLETED or done.returncode != 0:
        logger.warning("Video OCR: could not read the start time of %s; assuming 0", path.name)
        return 0.0
    try:
        start = float((done.stdout or "").strip().splitlines()[0])
    except (IndexError, ValueError):  # "N/A" or nothing: the file has no start offset
        return 0.0
    return max(start, 0.0)


def open_bitmap_source(
    config, path: Path, sub_index: int | None = None, cancel_event: threading.Event | None = None
) -> BitmapSource:
    """Resolve *path* (a video, a ``.sup``, or either half of a VobSub pair) to the image track to read.

    *sub_index* is the subtitle-track position (``-map 0:s:N``) chosen for a
    video; None takes the file's first image track (a .sup or a VobSub pair
    holds one). The canvas is the track's own size (a PGS track ffprobe did not
    size gives it in its first packet), else the video's, else 1920x1080, made
    even for the crop. Raises :class:`FrameSourceError` when the pair is
    incomplete, the track is missing or not an image track, or its timing
    cannot be read, and ``OperationCancelled`` when *cancel_event* stopped the
    packet reads (they read the whole file).
    """
    target = path
    if path.suffix.lower() in {".idx", ".sub"}:
        index = vobsub_index(path)
        if index is None:
            raise FrameSourceError(f"{path.name}: a VobSub subtitle needs its .idx and .sub files side by side")
        target = index
    ffprobe = resolve_ffprobe(config)
    images = [s for s in list_subtitle_streams(target, ffprobe) if not s.is_text]
    if sub_index is None:
        stream = images[0] if images else None
    else:
        stream = next((s for s in images if s.sub_index == sub_index), None)
    if stream is None:
        wanted = "" if sub_index is None else f" at position {sub_index}"
        raise FrameSourceError(f"{target.name}: no image subtitle track{wanted}")
    if stream.width is not None and stream.height is not None:
        width, height = stream.width, stream.height
    else:
        pgs = (
            pgs_composition_size(target, stream.sub_index, ffprobe, cancel_event)
            if stream.codec_name == "hdmv_pgs_subtitle"
            else None
        )
        width, height = pgs or get_video_size(target, ffprobe) or _FALLBACK_SIZE
    end = track_end_seconds(target, stream.sub_index, ffprobe, cancel_event)
    if end is None:
        raise FrameSourceError(f"{target.name}: the subtitle track's timing could not be read")
    # A bare image-subtitle file keeps its own clock: its "start" is its first cue.
    start = 0.0 if is_bitmap_file(target) else container_start_seconds(target, ffprobe)
    return BitmapSource(
        target,
        stream.sub_index,
        width - width % 2,
        height - height % 2,
        end - start + END_MARGIN_S,
        start_seconds=start,
    )


def ffmpeg_input_args(source: BitmapSource, fps: int) -> list[str]:
    """The ffmpeg inputs for *source*: a bounded colour canvas, then the subtitle file itself.

    ``-copyts`` keeps every cue on the file's own clock; ``-itsoffset`` moves a
    container that starts above 0 s back to the file start; ``-canvas_size``
    renders the track at the canvas's size; ``-t`` ends the canvas, which
    otherwise never ends.
    """
    size = f"{source.width}x{source.height}"
    canvas = f"color=c={CANVAS_COLOUR}:s={size}:r={fps}"
    shift = ["-itsoffset", f"{-source.start_seconds:.6f}"] if source.start_seconds else []
    subtitles = [*shift, "-canvas_size", size, "-i", str(source.path)]
    return ["-copyts", "-f", "lavfi", "-t", f"{source.end_seconds:.3f}", "-i", canvas, *subtitles]


def overlay_filter(source: BitmapSource) -> str:
    """The filter-graph head that draws the track on the canvas; the scan's crop/fps/scale chain follows it."""
    return f"[0:v][1:s:{source.sub_index}]overlay=eof_action=pass"

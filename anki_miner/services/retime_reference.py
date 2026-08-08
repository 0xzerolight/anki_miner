"""Choose and prepare the reference alass aligns a subtitle against.

alass takes either a video or **a subtitle file** as its reference:

    alass [OPTIONS] <reference-file> <incorrect-sub> <output>

Retiming used to always hand it a video, so every run paid for a full audio
extraction and then aligned against voice-activity detection. On anime that is
the weak path: BGM, sound effects and singing all read as "voice" to a VAD, so
the histogram alass matches is only loosely related to dialogue.

Almost every anime MKV already carries an embedded text subtitle track that is
timed exactly to that release. Aligning cue timings against cue timings removes
the guesswork *and* the extraction, so this module prefers an embedded subtitle
track and keeps the audio path as the fallback for raws.

Two things make the subtitle path safe:

* **Signs-only tracks are rejected.** A "Signs & Songs" track has a few dozen
  cues covering on-screen text, so aligning to it is worse than aligning to
  audio. They are filtered by title, by the ffprobe ``forced`` disposition, and
  finally by cue count after cleaning — the last check catches the ones that
  lie about what they are.
* **The reference is cleaned before use.** Comments, ASS drawings, karaoke and
  sign styles, and zero-length cues all skew the alignment. Cleaning touches
  only our own extracted temp file; the user's subtitle is never modified.

Nothing here raises: every failure degrades to the next candidate and ultimately
to the audio path, because a mediocre reference still beats refusing to retime.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pysubs2

from anki_miner.utils.audio_track_detector import (
    JAPANESE_LANGUAGE_CODES,
    SubtitleStream,
    list_subtitle_streams,
)
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

logger = logging.getLogger(__name__)

__all__ = [
    "ReferenceKind",
    "ReferenceOverride",
    "RetimeReference",
    "list_reference_subtitle_streams",
    "resolve_reference",
]

ReferenceKind = Literal["subtitle", "audio"]

ENGLISH_LANGUAGE_CODES = frozenset({"eng", "en", "english"})

#: Minimum cue count for a cleaned reference to be trusted as dialogue. A
#: 24-minute episode's dialogue track has several hundred cues; a signs-only
#: track that slipped the title and disposition filters has a few dozen.
_MIN_REFERENCE_CUES = 30

#: Stream titles that mark a track as something other than full dialogue.
_NON_DIALOGUE_TITLE_RE = re.compile(r"sign|song|karaoke|s&s|forced|commentary", re.IGNORECASE)

#: ASS style names that mark an event as something other than dialogue. The
#: alternation is bounded by non-word characters on both sides so a style named
#: ``Subtitle`` does not match ``title`` and ``Fixed`` does not match ``ed``.
_NON_DIALOGUE_STYLE_RE = re.compile(
    r"(?:^|[\W_])"
    r"(?:signs?|songs?|karaoke|kara|op|ed|opening|ending|credits?|titles?|captions?|notes?|typeset\w*)"
    r"(?:$|[\W_])",
    re.IGNORECASE,
)

#: ASS drawing-mode override. An event carrying one paints a vector shape, not
#: text, and its timing has nothing to do with speech.
_ASS_DRAWING_RE = re.compile(r"\\p[1-9]")


@dataclass(frozen=True)
class ReferenceOverride:
    """An explicit user pick of what to align against.

    ``index`` is a ``sub_index`` when *kind* is ``"subtitle"`` and an audio
    index (0-indexed among audio streams) when it is ``"audio"`` — the same
    numbering the track-picker dialogs and
    :meth:`MediaExtractorService.extract_full_audio` already use.
    """

    kind: ReferenceKind
    index: int


@dataclass(frozen=True)
class RetimeReference:
    """A prepared reference file, ready to hand to alass.

    ``temp`` is the file the caller must delete when done, or None when *path*
    is something we did not create. It is currently always equal to *path* or
    None, but the two are kept separate so ownership stays explicit.
    """

    path: Path
    kind: ReferenceKind
    temp: Path | None
    label: str


def list_reference_subtitle_streams(config, video: Path) -> list[SubtitleStream]:
    """Return *video*'s text subtitle streams, best reference candidate first.

    Exposed for the UI's reference picker so the order the user sees matches
    the order :func:`resolve_reference` would try. Non-text (bitmap) streams are
    dropped; unsuitable-looking ones are ranked last rather than hidden, so a
    user who knows better can still pick one.
    """
    streams = [s for s in list_subtitle_streams(video, resolve_ffprobe(config)) if s.is_text]
    return sorted(streams, key=_candidate_rank)


def resolve_reference(
    config,
    video: Path,
    *,
    override: ReferenceOverride | None = None,
    cancel_event: threading.Event | None = None,
    log_cb: Callable[[str], None] | None = None,
) -> RetimeReference | None:
    """Prepare the reference to align *video*'s subtitle against.

    With *override* None this prefers an embedded text subtitle track and falls
    back to extracted audio. With an override it honours the user's pick, and
    still falls back to audio if that pick turns out to be unusable — a mystery
    result beats a refused run, and the reason is logged either way.

    Returns None when even audio extraction fails; the caller then hands alass
    the raw video, which is exactly the pre-existing behaviour.
    """
    if _cancelled(cancel_event):
        return None

    if override is not None and override.kind == "audio":
        return _audio_reference(config, video, override.index, cancel_event, log_cb)

    if override is not None:
        picked = _stream_by_sub_index(config, video, override.index)
        if picked is not None:
            reference = _try_subtitle_stream(config, video, picked, cancel_event, log_cb)
            if reference is not None:
                return reference
        _log(log_cb, f"Chosen subtitle track {override.index + 1} is unusable; using audio instead.")
        return _audio_reference(config, video, None, cancel_event, log_cb)

    for stream in list_reference_subtitle_streams(config, video):
        if _cancelled(cancel_event):
            return None
        if _is_non_dialogue_stream(stream):
            _log(log_cb, f"Skipping subtitle track {stream.sub_index + 1}: not a dialogue track.")
            continue
        reference = _try_subtitle_stream(config, video, stream, cancel_event, log_cb)
        if reference is not None:
            return reference

    _log(log_cb, "No usable embedded subtitle track; aligning against audio.")
    return _audio_reference(config, video, None, cancel_event, log_cb)


# ---------------------------------------------------------------------------
# Subtitle references
# ---------------------------------------------------------------------------


def _candidate_rank(stream: SubtitleStream) -> tuple:
    """Sort key ordering reference candidates best-first.

    Dialogue before signs, then Japanese before English before everything else
    (a same-language reference has the closest cue boundaries), then the default
    track, then demuxer order as the tiebreak.
    """
    if stream.language_tag in JAPANESE_LANGUAGE_CODES:
        language_rank = 0
    elif stream.language_tag in ENGLISH_LANGUAGE_CODES:
        language_rank = 1
    else:
        language_rank = 2
    return (_is_non_dialogue_stream(stream), language_rank, not stream.is_default, stream.sub_index)


def _is_non_dialogue_stream(stream: SubtitleStream) -> bool:
    """True when *stream* advertises itself as signs/songs/forced/commentary."""
    if stream.is_forced:
        return True
    return bool(stream.title and _NON_DIALOGUE_TITLE_RE.search(stream.title))


def _stream_by_sub_index(config, video: Path, sub_index: int) -> SubtitleStream | None:
    """Return the text subtitle stream at *sub_index*, or None."""
    return next(
        (s for s in list_subtitle_streams(video, resolve_ffprobe(config)) if s.is_text and s.sub_index == sub_index),
        None,
    )


def _try_subtitle_stream(
    config,
    video: Path,
    stream: SubtitleStream,
    cancel_event: threading.Event | None,
    log_cb: Callable[[str], None] | None,
) -> RetimeReference | None:
    """Extract and clean *stream*, or return None when it is not usable."""
    extracted = _extract_stream(config, video, stream, cancel_event)
    if extracted is None:
        return None

    cleaned = extracted.with_suffix(".clean.srt")
    try:
        cue_count = _clean_reference(extracted, cleaned)
    except Exception:  # noqa: BLE001 — an unparsable track is just a bad candidate
        logger.warning("retime reference: could not clean %s", extracted, exc_info=True)
        cue_count = 0
    finally:
        _unlink(extracted)

    if cue_count < _MIN_REFERENCE_CUES:
        _unlink(cleaned)
        _log(
            log_cb,
            f"Skipping subtitle track {stream.sub_index + 1}: only {cue_count} dialogue lines.",
        )
        return None

    label = _stream_label(stream)
    _log(log_cb, f"Aligning against embedded subtitle track {stream.sub_index + 1} ({label}, {cue_count} lines).")
    return RetimeReference(path=cleaned, kind="subtitle", temp=cleaned, label=label)


def _extract_stream(
    config,
    video: Path,
    stream: SubtitleStream,
    cancel_event: threading.Event | None,
) -> Path | None:
    """Extract *stream* to a temp file, or return None. Never raises.

    Embedded-subtitle extraction already exists on :class:`AudioCondenserService`
    (it refuses bitmap streams and picks the right ``.ass``/``.srt`` extension),
    so this reuses it rather than growing a second ffmpeg call site.
    """
    # Lazy import: the condenser pulls in the whole ffmpeg surface, and this
    # module is imported by the retimer on every run.
    from anki_miner.services.audio_condenser import AudioCondenserService

    temp_dir = config.media_temp_folder
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        return AudioCondenserService(config).extract_embedded_subtitle(
            video, stream, temp_dir, cancel_event=cancel_event
        )
    except Exception:  # noqa: BLE001 — extraction is best-effort
        logger.warning("retime reference: subtitle extraction raised for s:%d", stream.sub_index, exc_info=True)
        return None


def _clean_reference(src: Path, dest: Path) -> int:
    """Write the dialogue-only cues of *src* to *dest* as UTF-8 SRT.

    Returns the number of cues written. Drops ASS comments, drawing events,
    non-dialogue styles, zero/negative-duration cues, and duplicate spans.
    Only cue *timings* matter to alass, but the text is carried through so the
    temp file stays readable when a run needs diagnosing.
    """
    try:
        subs = pysubs2.load(str(src))
    except UnicodeDecodeError as exc:
        subs = load_with_fallback_encoding(src, exc)

    kept: list[pysubs2.SSAEvent] = []
    seen: set[tuple[int, int]] = set()
    for event in subs.events:
        if getattr(event, "is_comment", None) is True:
            continue
        if event.end <= event.start:
            continue
        if _NON_DIALOGUE_STYLE_RE.search(event.style or ""):
            continue
        if _ASS_DRAWING_RE.search(event.text or ""):
            continue
        span = (event.start, event.end)
        if span in seen:
            continue
        seen.add(span)
        # SRT has no concept of an empty cue; substitute a marker so the writer
        # cannot merge or drop a span whose text was pure override tags.
        text = event.plaintext.strip() or "-"
        kept.append(pysubs2.SSAEvent(start=event.start, end=event.end, text=text))

    out = pysubs2.SSAFile()
    out.events = kept
    out.save(str(dest), encoding="utf-8", format_="srt")
    return len(kept)


def _stream_label(stream: SubtitleStream) -> str:
    """Human-readable identification of *stream* for logs and the UI."""
    language = stream.language_tag or "und"
    if stream.title:
        return f"{language} · {stream.title}"
    return language


# ---------------------------------------------------------------------------
# Audio reference (pre-existing behaviour)
# ---------------------------------------------------------------------------


def _audio_reference(
    config,
    video: Path,
    audio_track_override: int | None,
    cancel_event: threading.Event | None,
    log_cb: Callable[[str], None] | None,
) -> RetimeReference | None:
    """Extract the chosen audio track to a temp 16 kHz mono WAV for alass.

    Pre-extracting rather than letting alass pick a stream itself is deliberate:
    alass has no track flag, so on dual-audio anime it may align a Japanese
    subtitle against the English dub.
    :meth:`MediaExtractorService.extract_full_audio` auto-detects the Japanese
    track when *audio_track_override* is None.

    Returns None when extraction fails or is cancelled; the caller then falls
    back to handing alass the raw video, so a probe hiccup never blocks a run.
    """
    if _cancelled(cancel_event):
        return None

    from anki_miner.services.media_extractor import MediaExtractorService

    fd, tmp_name = tempfile.mkstemp(suffix=".retime-ref.wav")
    os.close(fd)
    tmp_wav = Path(tmp_name)
    try:
        ok = MediaExtractorService(config).extract_full_audio(
            video,
            tmp_wav,
            track_override=audio_track_override,
            cancel_event=cancel_event,
        )
    except Exception:  # noqa: BLE001 — extraction is best-effort; fall back to video
        logger.warning("retime: audio pre-extraction raised; using raw video", exc_info=True)
        ok = False

    if not ok:
        _unlink(tmp_wav)
        return None

    label = "auto-detected Japanese" if audio_track_override is None else f"track {audio_track_override + 1}"
    _log(log_cb, f"Aligning against audio ({label}).")
    return RetimeReference(path=tmp_wav, kind="audio", temp=tmp_wav, label=label)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def _log(log_cb: Callable[[str], None] | None, message: str) -> None:
    logger.debug("retime reference: %s", message)
    if log_cb is not None:
        log_cb(message)

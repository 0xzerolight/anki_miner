"""Audio Condenser — pure interval math + subtitle I/O (service part 1).

This module holds the ffmpeg-free, Qt-free, MeCab-free half of the Audio
Condenser tool: parse a subtitle file, shift every cue by a fixed offset,
drop non-dialogue lines, build padded/merged "keep" periods, project cues
into the condensed timeline, and write condensed SRT/LRC sidecars.

The interval math and subtitle I/O is expressed in **integer milliseconds** and
lives in plain module-level functions so it can be unit-tested without any
external process. The second half of the file adds
:class:`AudioCondenserService` (ffmpeg orchestration): it composes those pure
functions plus a :class:`MediaExtractorService` (deliberate same-package private
reuse of ``_resolve_audio_track_global_index`` / ``_check_encoder_available``)
and drives a single streaming ffmpeg pass per file.

Reference frame (binding, see design D1/D3/D4):

* The offset is applied **once**, in :func:`shift_events`.
* :func:`build_periods` and :func:`map_events_to_condensed` both consume the
  *already-shifted* events, so periods and condensed cues share one frame — an
  offset parameter on ``build_periods`` would make correct condensed-sub
  mapping impossible for a non-zero offset.
* Padding is applied first, then the period start is floored at 0 (never
  before padding — that was the original pad-after-clamp bug). The trailing
  pad on the final period is intentionally **not** stripped (D3).
"""

from __future__ import annotations

import collections
import contextlib
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pysubs2

from anki_miner.services.asr.srt_writer import segments_to_srt
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg
from anki_miner.utils.subprocess_utils import no_window_kwargs
from anki_miner.utils.text_utils import strip_subtitle_markup

if TYPE_CHECKING:
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.utils.audio_track_detector import SubtitleStream

logger = logging.getLogger(__name__)

# ``(start_ms, end_ms, text)`` cue and ``(start_ms, end_ms)`` keep-period.
Event = tuple[int, int, str]
Period = tuple[int, int]

# Whole-line bracket pairs that mark a non-dialogue line (aside / SFX / stage
# direction). Checked against the markup-stripped line only.
_BRACKET_PAIRS: tuple[tuple[str, str], ...] = (
    ("(", ")"),
    ("（", "）"),
    ("[", "]"),
    ("{", "}"),
)


# ---------------------------------------------------------------------------
# Subtitle loading (pysubs2 + encoding fallback, D10)
# ---------------------------------------------------------------------------


def load_subtitle_events(path: str | Path) -> list[Event]:
    """Load *path* into ``(start_ms, end_ms, text)`` tuples.

    Uses pysubs2 with a UTF-8 default; on a decode failure it retries with
    ``cp932`` first (the dominant non-UTF-8 input), then — only if cp932 also
    fails to decode — with a charset-normalizer-detected encoding, and finally
    re-raises the original UTF-8 error (D10). ``Comment`` events are skipped.
    Times come straight from ``event.start`` / ``event.end`` (millisecond
    ints); text is the raw cue text — markup stripping happens later in
    :func:`filter_lines`.
    """
    path = Path(path)
    try:
        subs = pysubs2.load(str(path))
    except UnicodeDecodeError as utf8_error:
        subs = _load_with_fallback_encoding(path, utf8_error)
    except pysubs2.exceptions.FormatAutodetectionError:
        # Empty (or contentless) file — no cues to condense.
        return []

    return [(event.start, event.end, event.text) for event in subs if not event.is_comment]


def _load_with_fallback_encoding(path: Path, original_error: UnicodeDecodeError) -> pysubs2.SSAFile:
    """Retry loading *path* with cp932, then a detected encoding (D10).

    cp932 is tried before the charset-normalizer detector on purpose: the
    detector confidently mis-detects real cp932 Japanese as ``cp949`` and
    decodes it *without* raising (silent mojibake), so for the app's dominant
    non-UTF-8 input the explicit cp932 attempt must win first. Only if cp932
    itself raises :class:`UnicodeDecodeError` do we consult the (soft-imported)
    detector; if that also fails, *original_error* (the UTF-8 error) is raised.
    """
    try:
        return pysubs2.load(str(path), encoding="cp932")
    except UnicodeDecodeError:
        pass
    encoding = _detect_encoding(path)
    if encoding:
        try:
            return pysubs2.load(str(path), encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    raise original_error


def _detect_encoding(path: Path) -> str | None:
    """Best-guess encoding for *path* via charset-normalizer, or None.

    charset-normalizer is soft-imported so its absence simply means the
    detector leg of :func:`_load_with_fallback_encoding` is skipped (the cp932
    attempt there runs first and independently).
    """
    try:
        from charset_normalizer import from_path
    except ImportError:
        return None
    match = from_path(str(path)).best()
    return match.encoding if match is not None else None


# ---------------------------------------------------------------------------
# Interval math
# ---------------------------------------------------------------------------


def shift_events(events: list[Event], offset_ms: int) -> list[Event]:
    """Return *events* with ``offset_ms`` added to every cue (applied once).

    Times may go negative; the t=0 floor is handled downstream by
    :func:`build_periods` (period start) and :func:`map_events_to_condensed`
    (output timestamps).
    """
    return [(start + offset_ms, end + offset_ms, text) for start, end, text in events]


def filter_lines(events: list[Event], filtered_chars: str) -> list[Event]:
    """Drop non-dialogue lines and clean the survivors' text.

    For each cue: strip subtitle markup; drop lines wholly enclosed in one of
    the bracket pairs ``()`` / ``（）`` / ``[]`` / ``{}`` (checked after the
    markup strip); remove every character in ``filtered_chars``; then drop any
    line left empty or whitespace-only. Surviving text is whitespace-collapsed
    to a single line (``\\N`` markers become spaces via the markup strip).
    """
    removal = {ord(char): None for char in filtered_chars}
    result: list[Event] = []
    for start, end, text in events:
        cleaned = " ".join(strip_subtitle_markup(text).split())
        if _is_whole_line_bracketed(cleaned):
            continue
        if removal:
            cleaned = " ".join(cleaned.translate(removal).split())
        if not cleaned:
            continue
        result.append((start, end, cleaned))
    return result


def _is_whole_line_bracketed(text: str) -> bool:
    """True iff *text* opens and closes with one bracket pair (whole-line)."""
    if len(text) < 2:
        return False
    return any(text[0] == open_c and text[-1] == close_c for open_c, close_c in _BRACKET_PAIRS)


def build_periods(events: list[Event], padding_ms: int) -> list[Period]:
    """Build padded, merged keep-periods from *already-shifted* events.

    Each cue is padded by ``padding_ms`` on both sides, the period start is
    floored at 0 **after** padding, then periods are sorted and overlapping or
    adjacent ones merged. A cue shifted fully before t=0 (its padded end is
    ``<= 0``) is dropped rather than emitted as an inverted period. The trailing
    pad on the last period is kept (D3).
    """
    intervals: list[Period] = []
    for start, end, _text in events:
        period_start = max(0, start - padding_ms)
        period_end = end + padding_ms
        # A cue shifted fully before t=0 (end + padding <= 0) would clamp to an
        # inverted (0, negative) period; dropping it here keeps every downstream
        # condensed-timestamp non-negative (D3/D4).
        if period_start >= period_end:
            continue
        intervals.append((period_start, period_end))
    intervals.sort()

    merged: list[Period] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def map_events_to_condensed(events: list[Event], periods: list[Period]) -> list[Event]:
    """Project *events* onto the condensed timeline defined by *periods* (D4).

    ``periods`` must be the sorted, non-overlapping output of
    :func:`build_periods` over the *same* filtered/shifted events. Each cue is
    intersected with every period; a non-empty intersection is clamped into the
    period and mapped as ``out_start + (t - period_start)``, where
    ``out_start`` is the cumulative duration of earlier periods. A cue spanning
    two periods emits one clamped cue per intersection; cues whose intersection
    is empty (only possible at the t=0 boundary under a negative offset) are
    dropped. No output timestamp is ever negative.
    """
    result: list[Event] = []
    out_start = 0
    for period_start, period_end in periods:
        for cue_start, cue_end, text in events:
            lo = max(cue_start, period_start)
            hi = min(cue_end, period_end)
            if lo >= hi:
                continue
            result.append((out_start + (lo - period_start), out_start + (hi - period_start), text))
        out_start += period_end - period_start
    return result


# ---------------------------------------------------------------------------
# Condensed subtitle writers
# ---------------------------------------------------------------------------


def write_condensed_srt(events: list[Event], path: str | Path) -> None:
    """Write condensed *events* to *path* as SRT (ms → s conversion).

    Thin wrapper over :func:`anki_miner.services.asr.srt_writer.segments_to_srt`,
    which may drop zero-duration / empty cues (acceptable here).
    """
    segments = [(start / 1000, end / 1000, text) for start, end, text in events]
    segments_to_srt(segments, Path(path))


def write_condensed_lrc(events: list[Event], path: str | Path) -> None:
    """Write condensed *events* to *path* as LRC.

    Standard condensed-audio LRC shape: one ``[mm:ss.xx]text`` line per cue
    followed by a bare ``[mm:ss.xx]`` end-tag line. Timestamps are
    centisecond-resolution; minutes may exceed 59 (there is no hours field).
    """
    lines: list[str] = []
    for start, end, text in events:
        lines.append(f"[{_format_lrc_timestamp(start)}]{text}")
        lines.append(f"[{_format_lrc_timestamp(end)}]")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _format_lrc_timestamp(ms: int) -> str:
    """Format *ms* as an LRC ``mm:ss.xx`` timestamp (centiseconds)."""
    total_cs = round(ms / 10)
    minutes, rem_cs = divmod(total_cs, 6000)
    seconds, centis = divmod(rem_cs, 100)
    return f"{minutes:02d}:{seconds:02d}.{centis:02d}"


# ---------------------------------------------------------------------------
# ffmpeg orchestration (service part 2, see design D2/D7/D8/D9)
# ---------------------------------------------------------------------------

# Suffix -> (ffmpeg audio encoder, uses -b:a bitrate, downmix to stereo). opus
# (libopus) rejects >2ch input (5.1 eac3/ac3 is the common anime BD/WEB-DL case),
# so it always gets ``-ac 2`` — mirrors media_extractor.py:1064-1070. flac is
# lossless, so it takes neither a bitrate nor a channel remap.
_ENCODER_SETTINGS: dict[str, tuple[str, bool, bool]] = {
    ".mp3": ("libmp3lame", True, False),
    ".opus": ("libopus", True, True),
    ".flac": ("flac", False, False),
}

# Encoders worth pre-probing: libmp3lame/libopus are external and may be absent
# from a stripped ffmpeg build. flac is a built-in encoder — never missing — so
# it is not probed (a probe spawns a process; skip the cost).
_PROBE_REQUIRED: frozenset[str] = frozenset({"libmp3lame", "libopus"})

# A ``-progress pipe:1`` line is ``key=value`` where *key* is lowercase snake_case
# (frame, out_time_us, progress, ...). Anything whose pre-``=`` token is not a
# bare identifier (ffmpeg banner/error lines, e.g. ``[libopus @ 0x..] bad``) is
# routed to the diagnostic tail instead of parsed as progress.
_PROGRESS_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")


class EncoderUnavailableError(Exception):
    """Raised by :meth:`AudioCondenserService.condense` when the required audio
    encoder is missing from the ffmpeg build.

    Distinct from a plain ``return False`` so a batch worker can abort the whole
    queue **once** (every file would hit the same missing encoder) instead of
    grinding through N identical failures.
    """


def build_aselect_graph(periods: list[Period]) -> str:
    """Build the ``aselect``/``asetpts`` filter graph selecting *periods*.

    Emits ``aselect='between(t,a1,b1)+between(t,a2,b2)+...',asetpts=N/SR/TB`` with
    every bound converted from **integer milliseconds to float seconds** (the
    unit ``between(t,...)`` expects). ``asetpts=N/SR/TB`` restamps the surviving
    samples into one gapless timeline. Periods beyond EOF simply select nothing
    (harmless). Callers must reject an empty *periods* list before reaching here
    (an empty graph would select the whole stream).
    """
    terms = "+".join(f"between(t,{start / 1000:.3f},{end / 1000:.3f})" for start, end in periods)
    return f"aselect='{terms}',asetpts=N/SR/TB"


def _encoder_settings(suffix: str) -> tuple[str, bool, bool]:
    """Resolve an output-file *suffix* to ``(encoder, uses_bitrate, downmix)``."""
    try:
        return _ENCODER_SETTINGS[suffix.lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported condenser output suffix: {suffix!r}") from exc


def _safe_int(value: str, default: int | None) -> int | None:
    """Parse *value* as int; return *default* if it is not a plain integer."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _terminate_process(proc: subprocess.Popen) -> None:
    """Kill *proc*, swallowing the races where it has already exited."""
    with contextlib.suppress(OSError):
        proc.kill()


class AudioCondenserService:
    """ffmpeg orchestration for the Audio Condenser tool.

    Composes a :class:`MediaExtractorService` purely to reuse its private
    ``_resolve_audio_track_global_index`` (audio-track selection) and
    ``_check_encoder_available`` (encoder probe) — a deliberate same-package
    reuse documented in design D1. Callers may inject an *extractor* (tests do);
    otherwise one is built from *config*.
    """

    def __init__(self, config: AnkiMinerConfig, extractor: MediaExtractorService | None = None) -> None:
        self.config = config
        self.extractor = extractor if extractor is not None else MediaExtractorService(config)

    # -- Embedded subtitle extraction (D9) --------------------------------

    def extract_embedded_subtitle(
        self,
        video: Path,
        stream: SubtitleStream,
        out_dir: Path,
        cancel_event: threading.Event | None = None,
    ) -> Path | None:
        """Extract text subtitle *stream* from *video* into *out_dir*.

        Bitmap streams (``stream.is_text`` False) carry rendered images, not
        text, so they are refused with a log line and ``None`` — never handed to
        ffmpeg. The output extension is ``.ass`` for ``ass``/``ssa`` sources and
        ``.srt`` otherwise (subrip/webvtt/mov_text all transcode cleanly to SRT).
        Returns the written path on success (the **caller owns and deletes it**),
        or ``None`` on failure (a partial file, if any, is cleaned up here).
        """
        if not stream.is_text:
            logger.warning(
                "Refusing bitmap subtitle stream s:%d (codec=%s) in %s — not extractable as text.",
                stream.sub_index,
                stream.codec_name,
                video,
            )
            return None

        codec = (stream.codec_name or "").lower()
        ext = ".ass" if codec in ("ass", "ssa") else ".srt"
        out_path = out_dir / f"{video.stem}.s{stream.sub_index}{ext}"

        cmd = [
            resolve_ffmpeg(self.config),
            "-y",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(video),
            "-map",
            f"0:s:{stream.sub_index}",
            str(out_path),
        ]

        ok = self._run_streaming(
            cmd,
            total_period_ms=0,
            timeout=300.0,
            progress_cb=None,
            cancel_event=cancel_event,
        )
        if ok:
            return out_path

        # Failure/cancel: drop any partial file — the caller gets None and has no
        # handle to clean it up itself.
        with contextlib.suppress(OSError):
            out_path.unlink()
        return None

    # -- Single-pass condense (D2) ----------------------------------------

    def condense(
        self,
        media: Path,
        periods: list[tuple[int, int]],
        out_audio: Path,
        *,
        audio_track_override: int | None = None,
        bitrate_kbps: int = 96,
        progress_cb: Callable[[int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        """Condense *media* down to only *periods* of audio, writing *out_audio*.

        Runs a single streaming ffmpeg pass (design D2): one decode, exact PTS,
        no per-segment temp files or concat. The encoder is derived from
        ``out_audio.suffix`` (``.mp3`` → libmp3lame, ``.opus`` → libopus + stereo
        downmix, ``.flac`` → flac). libmp3lame/libopus are pre-probed and a
        missing encoder raises :class:`EncoderUnavailableError` (so a batch
        aborts once). An empty *periods* list returns ``False`` immediately
        (never runs ffmpeg with a select-nothing graph). Progress is reported
        0–100 via *progress_cb* off the ``-progress`` stream; *cancel_event*
        kills the in-flight process. Returns ``True`` only on a clean exit.
        """
        if not periods:
            logger.warning("Condense called with no keep-periods for %s — nothing to do.", media)
            return False

        encoder, uses_bitrate, downmix = _encoder_settings(out_audio.suffix)
        if encoder in _PROBE_REQUIRED and not self.extractor._check_encoder_available(encoder):
            raise EncoderUnavailableError(
                f"ffmpeg encoder {encoder!r} is unavailable in this build; "
                "install an ffmpeg with it or pick a different output format."
            )

        graph_text = build_aselect_graph(periods)
        graph_fd, graph_name = tempfile.mkstemp(
            suffix=".txt", prefix="condense_graph_", dir=str(self.config.media_temp_folder)
        )
        graph_path = Path(graph_name)
        try:
            with os.fdopen(graph_fd, "w", encoding="utf-8") as fh:
                fh.write(graph_text)

            global_index = self.extractor._resolve_audio_track_global_index(media, audio_track_override)

            cmd = [
                resolve_ffmpeg(self.config),
                "-y",
                "-hide_banner",
                "-nostdin",
                "-progress",
                "pipe:1",
                "-i",
                str(media),
            ]
            if global_index is not None:
                cmd += ["-map", f"0:{global_index}"]
            else:
                # Untagged single-track raws: mirror _extract_audio's 0:a:0 fallback.
                cmd += ["-map", "0:a:0"]
            cmd += ["-vn", "-sn", "-dn", "-filter_script:a", str(graph_path), "-c:a", encoder]
            if uses_bitrate:
                cmd += ["-b:a", f"{bitrate_kbps}k"]
            if downmix:
                cmd += ["-ac", "2"]
            cmd.append(str(out_audio))

            total_ms = sum(end - start for start, end in periods)
            # Generous ceiling: encoding a condensed track is far faster than
            # real time, but the input still has to be fully decoded, so scale to
            # the kept duration with a floor for tiny selections.
            timeout = max(600.0, total_ms / 1000 * 4)

            return self._run_streaming(
                cmd,
                total_period_ms=total_ms,
                timeout=timeout,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
            )
        finally:
            # The graph file is the ONLY temp this service owns (extracted subs
            # belong to the caller). Clean it on every path.
            with contextlib.suppress(OSError):
                graph_path.unlink()

    # -- Streaming runner (D7/D8) -----------------------------------------

    def _run_streaming(
        self,
        cmd: list[str],
        *,
        total_period_ms: int,
        timeout: float,
        progress_cb: Callable[[int], None] | None,
        cancel_event: threading.Event | None,
    ) -> bool:
        """Run *cmd* streaming, parsing ``-progress`` and honouring cancel/timeout.

        Modeled on ``subtitle_retimer._run_alass``: ``stderr`` is merged into the
        read pipe (an undrained stderr PIPE deadlocks ffmpeg on long inputs), the
        line loop parses ``key=value`` ``-progress`` records (``out_time_us`` /
        ``out_time_ms`` are BOTH microseconds — ffmpeg trac #7345), non-progress
        lines are kept in a bounded tail for failure diagnostics, and a watcher
        thread kills the process when *cancel_event* fires or *timeout* elapses.
        Returns ``True`` only on a clean (non-cancelled, exit-0) finish.
        """
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("Failed to launch ffmpeg (%s): %s", cmd[0], exc)
            return False

        done_event = threading.Event()
        timed_out = threading.Event()
        deadline = time.monotonic() + timeout

        def _watch() -> None:
            # Poll until the work finishes (done_event) — killing on cancel or
            # timeout. done_event.wait() returns True the instant it is set, so a
            # clean run exits the loop without ever touching the process.
            while not done_event.wait(0.05):
                if cancel_event is not None and cancel_event.is_set():
                    _terminate_process(proc)
                    return
                if time.monotonic() >= deadline:
                    timed_out.set()
                    _terminate_process(proc)
                    return

        watcher = threading.Thread(target=_watch, daemon=True, name="condenser-watcher")
        watcher.start()

        tail: collections.deque[str] = collections.deque(maxlen=50)

        if proc.stdout is None:  # pragma: no cover - stdout=PIPE always yields a pipe
            done_event.set()
            watcher.join()
            return False

        pending_us: int | None = None
        last_pct = -1
        try:
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                key, sep, value = line.partition("=")
                if not sep or not _PROGRESS_KEY_RE.match(key):
                    tail.append(line)
                    continue
                if key == "out_time_us":
                    pending_us = _safe_int(value, pending_us)
                elif key == "out_time_ms" and pending_us is None:
                    # ffmpeg quirk: out_time_ms is ALSO microseconds (trac #7345).
                    pending_us = _safe_int(value, pending_us)
                elif key == "progress":
                    last_pct = _emit_progress(progress_cb, pending_us, total_period_ms, last_pct)
                    if value == "end" and progress_cb is not None and last_pct < 100:
                        progress_cb(100)
                        last_pct = 100
                    pending_us = None
                # Other progress keys (frame=, speed=, ...) are ignored.
            proc.wait()
        finally:
            done_event.set()
            watcher.join()

        cancelled = cancel_event is not None and cancel_event.is_set()
        if not cancelled and proc.returncode == 0:
            if progress_cb is not None and last_pct < 100:
                progress_cb(100)
            return True

        if cancelled:
            return False

        logger.warning(
            "ffmpeg step failed (exit %s%s). Last output:\n%s",
            proc.returncode,
            ", timed out" if timed_out.is_set() else "",
            "\n".join(tail),
        )
        return False


def _emit_progress(
    progress_cb: Callable[[int], None] | None,
    pending_us: int | None,
    total_period_ms: int,
    last_pct: int,
) -> int:
    """Emit a 0–100 percent to *progress_cb* for the current ``-progress`` block.

    ``pending_us`` is the block's ``out_time`` in **microseconds**; percent is
    ``out_time_ms / sum(period_durations)`` clamped to 100. Emits only when the
    integer percent changes (progress is monotonic). Returns the new last-pct.
    """
    if progress_cb is None or total_period_ms <= 0 or pending_us is None:
        return last_pct
    pct = min(100, int(pending_us / 1000 / total_period_ms * 100))
    if pct != last_pct:
        progress_cb(pct)
    return pct

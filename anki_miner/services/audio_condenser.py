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
reuse of ``_resolve_audio_track_global_index`` / ``_check_encoder_available`` /
``_audio_filter_capability``) and drives a single streaming ffmpeg pass per file.

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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

import pysubs2

from anki_miner.services.asr.srt_writer import segments_to_srt
from anki_miner.services.audio_tagger import TaggingError, TrackMetadata, tag_audio_file
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.audio_track_detector import is_japanese_language_tag, list_subtitle_streams
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.file_pairing import find_sibling_subtitle, resolve_output_path
from anki_miner.utils.subprocess_utils import no_window_kwargs
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding
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

    Uses pysubs2 with a UTF-8 default; on a decode failure it dispatches on a
    UTF-16/32 BOM when one is present, otherwise retries with ``cp932`` first
    (the dominant non-BOM non-UTF-8 input), then — only if cp932 also fails to
    decode — with a charset-normalizer-detected encoding, and finally
    re-raises the original UTF-8 error (D10). ``Comment`` events are skipped.
    Times come straight from ``event.start`` / ``event.end`` (millisecond
    ints); text is the raw cue text — markup stripping happens later in
    :func:`filter_lines`.
    """
    path = Path(path)
    try:
        subs = pysubs2.load(str(path))
    except UnicodeDecodeError as utf8_error:
        subs = load_with_fallback_encoding(path, utf8_error)
    except pysubs2.exceptions.FormatAutodetectionError:
        # Empty (or contentless) file — no cues to condense.
        return []

    return [(event.start, event.end, event.text) for event in subs if not event.is_comment]


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
    """True iff *text* is a single balanced bracket span (whole-line).

    The opening bracket's matching close must be the **final** character. A line
    that merely starts and ends with brackets but carries dialogue between two
    separate spans (``（拍手）だが断る（ため息）`` — SFX caption + line + SFX
    caption) is dialogue and kept; a genuinely whole-bracketed aside
    (``（拍手）``) is dropped.
    """
    if len(text) < 2:
        return False
    for open_c, close_c in _BRACKET_PAIRS:
        if text[0] != open_c:
            continue
        depth = 0
        for index, char in enumerate(text):
            if char == open_c:
                depth += 1
            elif char == close_c:
                depth -= 1
                if depth == 0:
                    # First point the opening bracket balances: it is whole-line
                    # only if that close is the last character.
                    return index == len(text) - 1
        return False
    return False


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
    with atomic_write_path(Path(path)) as staged:
        staged.write_text("\n".join(lines) + "\n", encoding="utf-8")


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

# Ceiling for the embedded-subtitle demux (D9). Same full-demux cost class as
# MediaExtractorService.extract_full_audio (flat 30-minute ceiling): a large
# remux has to be fully demuxed even though only the subtitle stream is written,
# so the old flat 300 s could time out a valid multi-hour source. A ceiling, not
# a target — extraction is far faster than realtime in practice.
_EMBEDDED_SUBTITLE_TIMEOUT = 1800.0


class EncoderUnavailableError(Exception):
    """Raised by :meth:`AudioCondenserService.condense` when the required audio
    encoder is missing from the ffmpeg build.

    Distinct from a plain ``return False`` so a batch worker can abort the whole
    queue **once** (every file would hit the same missing encoder) instead of
    grinding through N identical failures.
    """


class FilterUnavailableError(Exception):
    """Raised by :meth:`AudioCondenserService.condense` when this ffmpeg build's
    ``aselect`` filter does not filter.

    Some builds (Ubuntu's ffmpeg 8.0.1-3ubuntu2 among them) ship an ``aselect``
    that passes every frame whatever its expression. The condense pass then
    "succeeds" and writes the WHOLE source track, so the user gets a full-length
    file named like a condensed one. Refusing is the only honest outcome.

    Fatal for a batch like :class:`EncoderUnavailableError`: every remaining file
    would hit the same binary.
    """


class _CondenseOutputIncomplete(Exception):
    """Internal control flow: discard a failed/cancelled staged output.

    Carries the step's :class:`FfmpegStepFailure` (None on cancel) so the reason
    survives unwinding out of the ``atomic_write_path`` context.
    """

    def __init__(self, failure: FfmpegStepFailure | None = None) -> None:
        super().__init__()
        self.failure = failure


#: Cap for the ffmpeg line quoted back to the user. The full tail already goes to
#: the log; the line that named the aselect-depth bug WAS the whole 4 KB filter
#: expression, and an Activity Log entry (and the Copy buffer behind it) must not
#: become a wall of ffmpeg output.
_FAILURE_REASON_MAX_CHARS = 200


@dataclass(frozen=True)
class FfmpegStepFailure:
    """Why a streaming ffmpeg step failed, condensed to one user-facing line.

    ``returncode`` is None when the process never launched. ``reason`` is the
    last meaningful line ffmpeg printed (its errors come last, so the *last*
    line is the diagnostic one — the first is the input banner), or the OSError
    text for a launch failure.
    """

    returncode: int | None
    timed_out: bool
    reason: str

    def summary(self) -> str:
        """One short line: what ffmpeg did and the last thing it said."""
        if self.returncode is None:
            head = "ffmpeg could not be started"
        elif self.timed_out:
            head = f"ffmpeg timed out (exit {self.returncode})"
        else:
            head = f"ffmpeg exited {self.returncode}"
        return f"{head}: {self.reason}" if self.reason else head


def _failure_reason(lines: Iterable[str], fallback: str = "") -> str:
    """Pick the most diagnostic line from ffmpeg's output tail, truncated."""
    reason = next((line.strip() for line in reversed(list(lines)) if line.strip()), fallback)
    if len(reason) > _FAILURE_REASON_MAX_CHARS:
        reason = reason[: _FAILURE_REASON_MAX_CHARS - 1] + "…"
    return reason


def build_aselect_graph(periods: list[Period]) -> str:
    """Build the ``aselect``/``asetpts`` filter graph selecting *periods*.

    Emits ``aselect='((between(t,a1,b1)+between(t,a2,b2))+(...))',asetpts=N/SR/TB``
    with every bound converted from **integer milliseconds to float seconds** (the
    unit ``between(t,...)`` expects). ``asetpts=N/SR/TB`` restamps the surviving
    samples into one gapless timeline. Periods beyond EOF simply select nothing
    (harmless).

    The ``+`` terms are folded into a BALANCED parenthesised tree, and that shape
    is load-bearing — ffmpeg's ``av_expr_parse`` has a fixed parser budget
    (``p.stack_index = 100``, libavutil/eval.c) that a real episode's period
    count blows in two different ways. Measured against ffmpeg 8 (libavutil 60):

    * flat ``a+b+c`` — parses at 100 terms, ``AVERROR(ENOMEM)`` at 101 and up.
    * every ``+`` parenthesised but LEFT- or RIGHT-leaning (i.e. the tempting
      ``functools.reduce(lambda a, b: f"({a}+{b})", terms)`` one-liner) — parses
      at 99, ``AVERROR(EINVAL)`` at 100 and up.
    * balanced — parses at 5000 terms, depth growing as log2 n.

    Only balanced clears both limits. A 25-minute episode yields ~125 periods
    after padding-merge, so the flat form failed on ordinary input for every
    bundle shipping ffmpeg 8 (ffmpeg 7 parses a 600-term flat chain happily,
    which is why this survived local runs and CI). Partial grouping is not a
    safe middle ground: flat runs of 50 joined by a balanced tree still failed
    at 101 terms. Do not "simplify" this back into a join or a reduce.
    """
    if not periods:
        raise ValueError("build_aselect_graph requires at least one period (an empty graph selects everything)")

    terms = [f"between(t,{start / 1000:.3f},{end / 1000:.3f})" for start, end in periods]
    while len(terms) > 1:
        terms = [f"({terms[i]}+{terms[i + 1]})" if i + 1 < len(terms) else terms[i] for i in range(0, len(terms), 2)]
    return f"aselect='{terms[0]}',asetpts=N/SR/TB"


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
    ``_resolve_audio_track_global_index`` (audio-track selection),
    ``_check_encoder_available`` (encoder probe) and ``_audio_filter_capability``
    (filter-file flag + ``aselect`` health) — a deliberate same-package reuse
    documented in design D1. Callers may inject an *extractor* (tests do);
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

        ok, _failure = self._run_streaming(
            cmd,
            total_period_ms=0,
            timeout=_EMBEDDED_SUBTITLE_TIMEOUT,
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
    ) -> tuple[bool, FfmpegStepFailure | None]:
        """Condense *media* down to only *periods* of audio, writing *out_audio*.

        Runs a single streaming ffmpeg pass (design D2): one decode, exact PTS,
        no per-segment temp files or concat. The encoder is derived from
        ``out_audio.suffix`` (``.mp3`` → libmp3lame, ``.opus`` → libopus + stereo
        downmix, ``.flac`` → flac). libmp3lame/libopus are pre-probed and a
        missing encoder raises :class:`EncoderUnavailableError` (so a batch
        aborts once), and an ffmpeg whose ``aselect`` does not filter raises
        :class:`FilterUnavailableError` the same way. An empty *periods* list
        fails immediately (never runs
        ffmpeg with a select-nothing graph). Progress is reported 0–100 via
        *progress_cb* off the ``-progress`` stream; *cancel_event* kills the
        in-flight process.

        Returns ``(ok, failure)`` — see :meth:`_run_streaming`. ``failure`` is
        None on both success and cancel.
        """
        if not periods:
            logger.warning("Condense called with no keep-periods for %s — nothing to do.", media)
            return False, FfmpegStepFailure(None, False, "no dialogue periods to keep")

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

            # One cached probe answers both "which spelling does this ffmpeg take"
            # (-filter_script was removed in ffmpeg 9, -/filter needs 7.0+) and
            # "does its aselect actually select". See _audio_filter_capability.
            filter_flag, aselect_ok = self.extractor._audio_filter_capability()
            if not aselect_ok:
                raise FilterUnavailableError(
                    "This ffmpeg build's 'aselect' filter does not filter, so condensing would "
                    "write the full-length track instead of a condensed one. Use the bundled "
                    "ffmpeg, or set a working one in Settings → Media."
                )

            try:
                with atomic_write_path(out_audio) as staged_audio:
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
                    cmd += ["-vn", "-sn", "-dn", filter_flag, str(graph_path), "-c:a", encoder]
                    if uses_bitrate:
                        cmd += ["-b:a", f"{bitrate_kbps}k"]
                    if downmix:
                        cmd += ["-ac", "2"]
                    cmd.append(str(staged_audio))

                    total_ms = sum(end - start for start, end in periods)
                    # Generous ceiling: encoding a condensed track is far faster than
                    # real time, but the input still has to be fully decoded, so scale to
                    # the kept duration with a floor for tiny selections.
                    timeout = max(600.0, total_ms / 1000 * 4)

                    ok, failure = self._run_streaming(
                        cmd,
                        total_period_ms=total_ms,
                        timeout=timeout,
                        progress_cb=progress_cb,
                        cancel_event=cancel_event,
                    )
                    if not ok:
                        raise _CondenseOutputIncomplete(failure)
            except _CondenseOutputIncomplete as incomplete:
                return False, incomplete.failure
            return True, None
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
    ) -> tuple[bool, FfmpegStepFailure | None]:
        """Run *cmd* streaming, parsing ``-progress`` and honouring cancel/timeout.

        Modeled on ``sync_engines/alass_engine.py`` (via ``utils/process_supervisor.run_supervised``,
        the retime pipeline's own supervised-subprocess runner): ``stderr`` is merged into the
        read pipe (an undrained stderr PIPE deadlocks ffmpeg on long inputs), the
        line loop parses ``key=value`` ``-progress`` records (``out_time_us`` /
        ``out_time_ms`` are BOTH microseconds — ffmpeg trac #7345), non-progress
        lines are kept in a bounded tail for failure diagnostics, and a watcher
        thread kills the process when *cancel_event* fires or *timeout* elapses.

        Returns ``(ok, failure)``. ``ok`` is True only on a clean (non-cancelled,
        exit-0) finish. ``failure`` carries the exit code / timed-out flag / last
        ffmpeg line for a real failure, and is None for both success and cancel
        (a cancel is the user's doing, not a fault to report). The pair is
        deliberately a tuple rather than a returned object: callers test the
        result for truth, and any struct — however "empty" — is truthy.
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
            return False, FfmpegStepFailure(None, False, _failure_reason([], str(exc)))

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

        with proc:  # closes pipes and waits on every path — no zombies
            if proc.stdout is None:  # pragma: no cover - stdout=PIPE always yields a pipe
                done_event.set()
                watcher.join()
                return False, FfmpegStepFailure(proc.returncode, False, "ffmpeg produced no output pipe")

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
                return True, None

            if cancelled:
                return False, None

            logger.warning(
                "ffmpeg step failed (exit %s%s). Last output:\n%s",
                proc.returncode,
                ", timed out" if timed_out.is_set() else "",
                "\n".join(tail),
            )
            return False, FfmpegStepFailure(proc.returncode, timed_out.is_set(), _failure_reason(tail))


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


# ---------------------------------------------------------------------------
# Per-file pipeline (ARC-015): product policy hoisted out of the QThread worker
# ---------------------------------------------------------------------------
#
# ``condense_one`` owns the full per-file pipeline that used to live on
# ``CondenseWorker``: subtitle-source priority chain, JP-track pick, the pure
# interval math, the ffmpeg condense pass, and best-effort sidecar writing. It
# returns a STRUCTURED :class:`CondenseResult` (a status code plus any values a
# message needs) — never a user-facing string. i18n stays in the GUI worker,
# which maps each :class:`CondenseStatus` back to a translated ``tr()`` message.
# This keeps the policy unit-testable without a QThread.
#
# ``EncoderUnavailableError`` and ``FilterUnavailableError`` are deliberately NOT
# caught here: they propagate out so the worker can re-raise them into the
# queue-stopping path (every remaining file would hit the same broken binary).

# Subtitle-source priority for the condenser (D9). Unlike the mining default it
# includes ``.vtt`` — the condenser accepts WebVTT sidecars (D12).
_CONDENSER_SUBTITLE_PRIORITY: tuple[str, ...] = (".ass", ".ssa", ".srt", ".vtt")


class CondenseStatus(Enum):
    """Outcome of :func:`condense_one` (mapped to a ``tr()`` message by the worker)."""

    SUCCESS = auto()
    #: No explicit / sibling / embedded subtitle source (embedded probe found nothing).
    NO_SOURCE = auto()
    #: A ``subtitle_track_override`` was given but no stream carries that ``sub_index``.
    SUBTITLE_TRACK_NOT_FOUND = auto()
    #: Only image-based (bitmap) embedded subtitle streams — nothing extractable as text.
    BITMAP_ONLY = auto()
    #: An embedded text stream was selected but ffmpeg extraction failed.
    EXTRACT_FAILED = auto()
    #: The subtitle parsed to zero keep-periods (empty / all-comment / all filtered).
    NO_DIALOGUE = auto()
    #: The ffmpeg condense pass returned False for a non-cancel reason.
    CONDENSE_FAILED = auto()
    #: A cancel landed during embedded extraction or the condense pass.
    CANCELLED = auto()


@dataclass(frozen=True)
class CondenseResult:
    """Structured result of :func:`condense_one`.

    ``out_audio`` is set only on :attr:`CondenseStatus.SUCCESS`. ``codecs`` carries
    the joined codec list for a :attr:`CondenseStatus.BITMAP_ONLY` message.
    ``sidecar_error`` is the raw exception string when the audio succeeded but the
    optional condensed SRT/LRC sidecar write failed (non-fatal — the worker
    surfaces it as a warning on an otherwise-successful result). ``tag_error``
    is the same contract for the optional metadata write (Issue #113): the
    audio is complete on disk, only the tags are missing.
    ``failure_reason`` is the short one-line ffmpeg diagnosis behind a
    :attr:`CondenseStatus.CONDENSE_FAILED` — that status covers a launch failure,
    a nonzero exit and a timeout alike, so without it the user sees three
    unrelated faults as one opaque message.
    """

    status: CondenseStatus
    out_audio: Path | None = None
    codecs: str | None = None
    sidecar_error: str | None = None
    failure_reason: str | None = None
    tag_error: str | None = None


def condense_one(
    service: AudioCondenserService,
    config: AnkiMinerConfig,
    media: Path,
    external_sub: Path | None,
    out_audio: Path,
    *,
    offset_ms: int = 0,
    padding_ms: int = 500,
    filtered_chars: str = "",
    bitrate_kbps: int = 96,
    audio_track_override: int | None = None,
    subtitle_track_override: int | None = None,
    write_subs: bool = False,
    metadata: TrackMetadata | None = None,
    progress_cb: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> CondenseResult:
    """Condense one media file: resolve a subtitle source, run the pipeline, write audio.

    Steps (see :class:`CondenseWorker` docstring for the product rationale):

    1. Resolve a subtitle source by priority — explicit *external_sub* → on-disk
       sibling → embedded text track (JP-tagged first). A miss returns the matching
       failure :class:`CondenseResult` without invoking ffmpeg.
    2. Load → shift (once, by *offset_ms*) → filter → build padded keep-periods.
       Zero periods → :attr:`CondenseStatus.NO_DIALOGUE`.
    3. Run the single-pass ffmpeg condense (progress forwarded via *progress_cb*).
    4. On success, optionally write condensed SRT/LRC sidecars and, when
       *metadata* is given, tag the finished audio (both best-effort — failures
       are reported via ``sidecar_error`` / ``tag_error``, never as a failed
       result).

    The extracted embedded-subtitle temp file (when one was created) is always
    deleted here before returning. :class:`EncoderUnavailableError` and
    :class:`FilterUnavailableError` from the condense pass are NOT caught — they
    propagate for the caller to stop the queue.
    """
    temp_sub: Path | None = None
    try:
        sub_path, temp_sub, failure = _resolve_subtitle_source(
            service, config, media, external_sub, subtitle_track_override, cancel_event
        )
        if failure is not None:
            return failure
        assert sub_path is not None  # failure is None ⇒ a source was resolved

        events = load_subtitle_events(sub_path)
        shifted = shift_events(events, offset_ms)
        filtered = filter_lines(shifted, filtered_chars)
        periods = build_periods(filtered, padding_ms)
        if not periods:
            return CondenseResult(CondenseStatus.NO_DIALOGUE)

        ok, step_failure = service.condense(
            media,
            periods,
            out_audio,
            audio_track_override=audio_track_override,
            bitrate_kbps=bitrate_kbps,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
        )
        if not ok:
            if _is_cancelled(cancel_event):
                return CondenseResult(CondenseStatus.CANCELLED)
            return CondenseResult(
                CondenseStatus.CONDENSE_FAILED,
                failure_reason=step_failure.summary() if step_failure is not None else None,
            )

        sidecar_error = _write_condensed_subs(filtered, periods, out_audio) if write_subs else None
        tag_error = _apply_tags(out_audio, metadata) if metadata is not None else None
        return CondenseResult(
            CondenseStatus.SUCCESS, out_audio=out_audio, sidecar_error=sidecar_error, tag_error=tag_error
        )
    finally:
        # Delete the extracted embedded-subtitle temp file (external / sibling
        # subs are user-owned and never touched). Runs on every path, including
        # the Encoder/FilterUnavailableError propagation.
        if temp_sub is not None:
            with contextlib.suppress(OSError):
                if temp_sub.exists():
                    temp_sub.unlink()


def _resolve_subtitle_source(
    service: AudioCondenserService,
    config: AnkiMinerConfig,
    media: Path,
    external_sub: Path | None,
    subtitle_track_override: int | None,
    cancel_event: threading.Event | None,
) -> tuple[Path | None, Path | None, CondenseResult | None]:
    """Resolve *media*'s subtitle source by priority (D9).

    Returns ``(sub_path, temp_sub, failure)``:
      * usable external / sibling / embedded sub → ``(path, temp_or_None, None)``
      * no usable source → ``(None, temp_or_None, CondenseResult)``

    ``temp_sub`` is the extracted embedded temp file (deleted by :func:`condense_one`
    in its ``finally``); it is None for external and sibling subs.
    """
    # 1. Explicit user-picked file (single mode).
    if external_sub is not None:
        return external_sub, None, None

    # 2. Sibling external sub (condenser priority, incl. .vtt).
    sibling = find_sibling_subtitle(media, priority=_CONDENSER_SUBTITLE_PRIORITY)
    if sibling is not None:
        return sibling, None, None

    # 3. Embedded text subtitle track.
    return _resolve_embedded_subtitle(service, config, media, subtitle_track_override, cancel_event)


def _resolve_embedded_subtitle(
    service: AudioCondenserService,
    config: AnkiMinerConfig,
    media: Path,
    subtitle_track_override: int | None,
    cancel_event: threading.Event | None,
) -> tuple[Path | None, Path | None, CondenseResult | None]:
    """Extract an embedded text subtitle from *media* (D9), or report why not."""
    streams = list_subtitle_streams(media, resolve_ffprobe(config))
    if not streams:
        return None, None, CondenseResult(CondenseStatus.NO_SOURCE)

    stream = _pick_subtitle_stream(streams, subtitle_track_override)
    if stream is None:
        if subtitle_track_override is not None:
            return None, None, CondenseResult(CondenseStatus.SUBTITLE_TRACK_NOT_FOUND)
        codecs = ", ".join(sorted({s.codec_name or "unknown" for s in streams}))
        return None, None, CondenseResult(CondenseStatus.BITMAP_ONLY, codecs=codecs)

    temp_dir = config.media_temp_folder
    temp_dir.mkdir(parents=True, exist_ok=True)
    extracted = service.extract_embedded_subtitle(media, stream, temp_dir, cancel_event=cancel_event)
    if extracted is None:
        if _is_cancelled(cancel_event):
            return None, None, CondenseResult(CondenseStatus.CANCELLED)
        return None, None, CondenseResult(CondenseStatus.EXTRACT_FAILED)
    return extracted, extracted, None


def _pick_subtitle_stream(streams: list[SubtitleStream], subtitle_track_override: int | None) -> SubtitleStream | None:
    """Choose a subtitle stream: override, then non-forced/Japanese/demux order."""
    if subtitle_track_override is not None:
        return next((s for s in streams if s.sub_index == subtitle_track_override), None)
    text_streams = [s for s in streams if s.is_text]
    if not text_streams:
        return None
    return min(
        text_streams,
        key=lambda stream: (
            stream.is_forced,
            not is_japanese_language_tag(stream.language_tag),
            stream.sub_index,
        ),
    )


def _write_condensed_subs(filtered_events: list[Event], periods: list[Period], out_audio: Path) -> str | None:
    """Write condensed SRT + LRC sidecars beside *out_audio*.

    Consumes the **filtered, shifted** events (D4) so the sidecars show only the
    audible dialogue. Returns None on success, or the raw exception string when a
    writer fails — the audio is already written, so this is non-fatal (the GUI
    worker wraps the string in a translated warning).
    """
    try:
        condensed = map_events_to_condensed(filtered_events, periods)
        srt_path = resolve_output_path(out_audio.parent, f"{out_audio.stem}.srt")
        lrc_path = resolve_output_path(out_audio.parent, f"{out_audio.stem}.lrc")
        write_condensed_srt(condensed, srt_path)
        write_condensed_lrc(condensed, lrc_path)
        return None
    except Exception as exc:  # noqa: BLE001 — sidecar failure must never fail an already-written audio
        logger.warning("condense_one: condensed subtitle write failed for %s: %s", out_audio, exc)
        return str(exc)


def _apply_tags(out_audio: Path, metadata: TrackMetadata) -> str | None:
    """Best-effort metadata write (Issue #113); the audio is already complete on disk.

    Returns None on success, or the raw error string for the worker to wrap in
    a translated warning — same contract as ``_write_condensed_subs``.
    """
    try:
        tag_audio_file(out_audio, metadata)
    except TaggingError as exc:
        logger.warning("condense_one: tagging failed for %s: %s", out_audio, exc)
        return str(exc)
    return None


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    """True when *cancel_event* is present and set."""
    return cancel_event is not None and cancel_event.is_set()

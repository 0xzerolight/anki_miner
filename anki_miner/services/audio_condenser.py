"""Audio Condenser — pure interval math + subtitle I/O (service part 1).

This module holds the ffmpeg-free, Qt-free, MeCab-free half of the Audio
Condenser tool: parse a subtitle file, shift every cue by a fixed offset,
drop non-dialogue lines, build padded/merged "keep" periods, project cues
into the condensed timeline, and write condensed SRT/LRC sidecars.

Everything here is expressed in **integer milliseconds** and is a plain
module-level function so it can be unit-tested without any external process.
A later task adds ``AudioCondenserService`` (ffmpeg orchestration) to this
same file; the pieces below deliberately keep clean, class-free interfaces so
that layer can compose them.

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

from pathlib import Path

import pysubs2

from anki_miner.services.asr.srt_writer import segments_to_srt
from anki_miner.utils.text_utils import strip_subtitle_markup

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

    Uses pysubs2 with a UTF-8 default; on a decode failure it detects the
    encoding with charset-normalizer, retries, and finally falls back to
    ``cp932`` (D10). ``Comment`` events are skipped. Times come straight from
    ``event.start`` / ``event.end`` (millisecond ints); text is the raw cue
    text — markup stripping happens later in :func:`filter_lines`.
    """
    path = Path(path)
    try:
        subs = pysubs2.load(str(path))
    except UnicodeDecodeError:
        subs = _load_with_fallback_encoding(path)
    except pysubs2.exceptions.FormatAutodetectionError:
        # Empty (or contentless) file — no cues to condense.
        return []

    return [(event.start, event.end, event.text) for event in subs if not event.is_comment]


def _load_with_fallback_encoding(path: Path) -> pysubs2.SSAFile:
    """Retry loading *path* with a detected encoding, else ``cp932``."""
    encoding = _detect_encoding(path)
    if encoding:
        try:
            return pysubs2.load(str(path), encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    return pysubs2.load(str(path), encoding="cp932")


def _detect_encoding(path: Path) -> str | None:
    """Best-guess encoding for *path* via charset-normalizer, or None.

    charset-normalizer is soft-imported so its absence just routes straight to
    the ``cp932`` fallback in :func:`_load_with_fallback_encoding`.
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
    adjacent ones merged. The trailing pad on the last period is kept (D3).
    """
    intervals: list[Period] = [(max(0, start - padding_ms), end + padding_ms) for start, end, _text in events]
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

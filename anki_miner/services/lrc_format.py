"""LRC lyrics as a subtitle format (MS-08).

An ``.lrc`` file times each lyric line with one or more ``[mm:ss.xx]`` stamps
and gives no end time: a line shows until the next one starts.
:func:`parse_lrc` turns that into ``(start_ms, end_ms, text)`` cues and
:class:`LrcFormat` hands them to pysubs2 through the extension point pysubs2's
``FormatBase`` documents. Once :func:`register_lrc_format` has run, every
``pysubs2.load`` in the app reads ``.lrc`` like any subtitle, the encoding
fallback ladder (``utils/subtitle_encoding.py``) included.

Rules:

* A line starts with one or more stamps: ``[mm:ss]``, ``[mm:ss.x]`` to
  ``[mm:ss.xxx]``, or ``[mm:ss:xx]``; minutes may pass 59. Each stamp makes one
  cue with the line's text.
* Enhanced-LRC word stamps (``<mm:ss.xx>``) are removed from the text.
* Any other line, bracketed metadata (``[ar:]``, ``[ti:]``, ``[offset:]``,
  ``[length:]``) included, is ignored. ``[offset:]`` is not applied: the
  subtitle offset does that job.
* A stamp with no text only ends the line before it.
* Stamps at the same time join line by line (bilingual lyrics), as SAMI cues
  do, so the mining language's bilingual-cue gate can drop the other line.
* A line ends at the next stamp, but at most ``MAX_LINE_MS`` after it starts;
  the last line lasts ``LAST_LINE_MS``.
"""

from __future__ import annotations

import re
from typing import Any, TextIO

import pysubs2
from pysubs2.formats import FILE_EXTENSION_TO_FORMAT_IDENTIFIER, FORMAT_IDENTIFIER_TO_FORMAT_CLASS
from pysubs2.formats.base import FormatBase

__all__ = ["LAST_LINE_MS", "LRC_FORMAT", "MAX_LINE_MS", "LrcFormat", "parse_lrc", "register_lrc_format"]

#: The pysubs2 format identifier, and the extension that names it.
LRC_FORMAT = "lrc"
_LRC_EXTENSION = ".lrc"
#: How long the last line lasts: nothing follows it to end it.
LAST_LINE_MS = 5000
#: The longest any line lasts. An instrumental break without a blank stamp
#: would otherwise stretch the line before it into a long, mostly-music clip.
MAX_LINE_MS = 10_000

_STAMP = r"\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]"
_STAMP_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_LINE_RE = re.compile(rf"^((?:{_STAMP})+)(.*)$")
_WORD_STAMP_RE = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
_GUESS_RE = re.compile(rf"^[\ufeff\s]*{_STAMP}", re.MULTILINE)


def _stamp_ms(minutes: str, seconds: str, fraction: str | None) -> int:
    """A stamp's minutes, seconds and digits after the separator, in milliseconds."""
    ms = (int(minutes) * 60 + int(seconds)) * 1000
    if fraction:
        ms += int(fraction.ljust(3, "0"))
    return ms


def parse_lrc(text: str) -> list[tuple[int, int, str]]:
    """The cues of an LRC file's *text*, in time order (rules in the module docstring)."""
    stamps: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        match = _LINE_RE.match(raw_line.strip().lstrip("\ufeff"))
        if match is None:
            continue
        lyric = " ".join(_WORD_STAMP_RE.sub("", match.group(2)).split())
        for stamp in _STAMP_RE.finditer(match.group(1)):
            minutes, seconds, fraction = stamp.groups()
            stamps.append((_stamp_ms(minutes, seconds, fraction), lyric))
    stamps.sort(key=lambda stamp: stamp[0])  # stable: lines at one time keep file order

    timeline: list[tuple[int, str]] = []
    for start, lyric in stamps:
        if timeline and timeline[-1][0] == start:
            if lyric:
                previous = timeline[-1][1]
                timeline[-1] = (start, "\\N".join((previous, lyric)) if previous else lyric)
            continue
        timeline.append((start, lyric))

    cues: list[tuple[int, int, str]] = []
    for index, (start, lyric) in enumerate(timeline):
        if not lyric:
            continue
        is_last = index + 1 == len(timeline)
        end = start + LAST_LINE_MS if is_last else min(timeline[index + 1][0], start + MAX_LINE_MS)
        cues.append((start, end, lyric))
    return cues


class LrcFormat(FormatBase):
    """pysubs2 reader for ``.lrc`` lyrics.

    Read only: Condense writes its own LRC (``audio_condenser.write_condensed_lrc``),
    and ``FormatBase.to_file`` raises ``NotImplementedError`` for anything else.
    """

    @classmethod
    def guess_format(cls, text: str) -> str | None:
        """``"lrc"`` when a line starts with a stamp; never for SRT/VTT or ASS text.

        An SRT or ASS file whose cue text happens to start with ``[01:23]``
        stays what it is: autodetection fails when two formats claim a file.
        """
        if "-->" in text or "[Script Info]" in text:
            return None
        return LRC_FORMAT if _GUESS_RE.search(text) else None

    @classmethod
    def from_file(cls, subs: pysubs2.SSAFile, fp: TextIO, format_: str, **kwargs: Any) -> None:
        """Append one event per cue :func:`parse_lrc` finds in *fp*."""
        for start, end, lyric in parse_lrc(fp.read()):
            subs.append(pysubs2.SSAEvent(start=start, end=end, text=lyric))


def register_lrc_format() -> None:
    """Let ``pysubs2.load`` read ``.lrc`` files (idempotent; MS-08)."""
    FORMAT_IDENTIFIER_TO_FORMAT_CLASS.setdefault(LRC_FORMAT, LrcFormat)
    FILE_EXTENSION_TO_FORMAT_IDENTIFIER.setdefault(_LRC_EXTENSION, LRC_FORMAT)

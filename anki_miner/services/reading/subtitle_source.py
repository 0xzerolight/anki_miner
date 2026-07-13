"""Load one subtitle file (.srt/.ass/.ssa/.vtt) into per-cue reading units.

Each surviving cue becomes one :class:`ReadingUnit` — the same sentence
granularity the video pipeline mines from the same file — with the cue's
start time as the ``location_label`` and no image. The cue loop (Comment-event
skip → ``clean_subtitle_text`` → drop empties) deliberately duplicates
``SubtitleParserService.parse_raw_entries``: reusing it would couple this
config-free loader to a config/MeCab-bound service.

v1 limitations vs the video path: ``config.subtitle_regex_filter`` and
``subtitle_offset`` do NOT apply here (reading loaders are config-free; an
offset is meaningless without media). Encoding handling is *broader* — the
video path is utf-8-only via ``pysubs2.load``, while this loader sniffs
BOM/utf-8/cp932/euc_jp like the aozora loader.

MicroDVD ``.sub`` is unsupported: it is frame-based and pysubs2 raises
``UnknownFPSError`` without a media-derived fps.
"""

from __future__ import annotations

import pysubs2

from anki_miner.exceptions import SetupError
from anki_miner.models.reading import (
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)
from anki_miner.services.reading._util import _decode
from anki_miner.utils.text_utils import clean_subtitle_text


def _format_cue_time(seconds: float) -> str:
    """Cue start as trimmed ``m:ss`` / ``h:mm:ss`` (not the video path's
    zero-padded HH:MM:SS — services can't import orchestration upward)."""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def load(ref: ReadingSourceRef) -> ReadingDocument:
    """Load a subtitle file into a per-cue :class:`ReadingDocument`.

    Identity mirrors the video path: series = parent folder name,
    episode = file stem.

    Raises:
        SetupError: unreadable file or unparseable subtitle content.
    """
    path = ref.path
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise SetupError(f"Cannot read subtitle file '{path.name}': {e}") from e

    text = _decode(raw)
    # detect() matched the lowered suffix but the ref keeps original case;
    # pysubs2's ext→format map is lowercase-keyed (".SRT" would raise).
    try:
        format_ = pysubs2.formats.get_format_identifier(path.suffix.lower())
        subs = pysubs2.SSAFile.from_string(text, format_=format_)
    except Exception as e:  # pysubs2 raises format-specific parse errors
        raise SetupError(f"Cannot parse subtitle file '{path.name}': {e}") from e

    units: list[ReadingUnit] = []
    for event in subs:
        # Skip ASS/SSA Comment events (same guard as parse_raw_entries).
        if getattr(event, "is_comment", None) is True:
            continue
        cue_text = clean_subtitle_text(event.text)
        if not cue_text:
            continue
        units.append(
            ReadingUnit(
                text=cue_text,
                index=len(units),
                location_label=_format_cue_time(event.start / 1000.0),
                image_ref=None,
            )
        )

    # pysubs2 parses garbage leniently (0 events, no error): a cue-less file
    # is almost certainly not a subtitle — fail the item with a reason instead
    # of mining silently to "0 cards".
    if not units:
        raise SetupError(f"No subtitle cues found in '{path.name}' — is it really a subtitle file?")

    return ReadingDocument(
        title=path.stem,
        kind="subtitle",
        series=path.parent.name,
        episode=path.stem,
        units=units,
    )

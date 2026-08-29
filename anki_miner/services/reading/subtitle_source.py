"""Load one subtitle file (.srt/.ass/.ssa/.vtt) into per-cue reading units.

Each surviving cue becomes one :class:`ReadingUnit` — the same sentence
granularity the video pipeline mines from the same file — with the cue's
start time as the ``location_label`` and no image. The cue loop (Comment-event
skip → ``clean_subtitle_text`` → drop empties) deliberately duplicates
``SubtitleParserService.parse_raw_entries``: reusing it would couple this
config-free loader to a config/MeCab-bound service.

Config-free by design: annotation stripping is unconditional inside
``clean_subtitle_text``; the configured ``subtitle_regex_filter`` still runs in
``SubtitleParserService.parse_text_units`` (``subtitle_cleanup=True``), the one
config/MeCab-bound seam. ``subtitle_offset`` never applies here (an offset is
meaningless without media). Encoding handling is *broader* — the video path is
utf-8-only via ``pysubs2.load``, while this loader sniffs BOM/utf-8/cp932/euc_jp
like the aozora loader.

MicroDVD ``.sub`` is unsupported: it is frame-based and pysubs2 raises
``UnknownFPSError`` without a media-derived fps.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pysubs2

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.models.reading import (
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)
from anki_miner.services.reading._util import _decode
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.text_utils import clean_subtitle_text

logger = logging.getLogger(__name__)

_MAX_TEXT_FILE_BYTES = 32 * 1024 * 1024


def _format_cue_time(seconds: float) -> str:
    """Cue start as trimmed ``m:ss`` / ``h:mm:ss`` (not the video path's
    zero-padded HH:MM:SS — services can't import orchestration upward)."""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _raise_if_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise OperationCancelled("Reading load cancelled")


def load(
    ref: ReadingSourceRef,
    *,
    cancel_check: Callable[[], bool] | None = None,
    encodings: tuple[str, ...] | None = None,
) -> ReadingDocument:
    """Load a subtitle file into a per-cue :class:`ReadingDocument`.

    Identity mirrors the video path: series = parent folder name,
    episode = file stem. ``encodings`` is the mining language's decode ladder;
    ``None`` keeps the built-in Japanese sniffing path (see ``_util._decode``).

    Raises:
        SetupError: unreadable file or unparseable subtitle content.
    """
    _raise_if_cancelled(cancel_check)
    # Per-kind ref contract: file-backed kinds always carry a path.
    assert ref.path is not None
    path = ref.path
    try:
        size = path.stat().st_size
        if size > _MAX_TEXT_FILE_BYTES:
            raise SetupError(
                f"subtitle file '{path.name}' is {size:,} bytes (cap {_MAX_TEXT_FILE_BYTES:,}); refusing to load"
            )
        with path.open("rb") as f:
            raw = f.read(_MAX_TEXT_FILE_BYTES + 1)
        _raise_if_cancelled(cancel_check)
        if len(raw) > _MAX_TEXT_FILE_BYTES:
            raise SetupError(
                f"subtitle file '{path.name}' exceeds cap {_MAX_TEXT_FILE_BYTES:,} bytes; refusing to load"
            )
    except OSError as e:
        logger.debug("Subtitle read failed: file=%s error=%s detail=%s", path, type(e).__name__, e)
        raise SetupError(f"Cannot read subtitle file '{path.name}': {e}") from e

    text = _decode(raw, encodings=encodings)
    # detect() matched the lowered suffix but the ref keeps original case;
    # pysubs2's ext→format map is lowercase-keyed (".SRT" would raise).
    try:
        format_ = pysubs2.formats.get_format_identifier(path.suffix.lower())
        subs = pysubs2.SSAFile.from_string(text, format_=format_)
    except Exception as e:  # pysubs2 raises format-specific parse errors
        _raise_if_cancelled(cancel_check)
        # Parser exceptions can contain cue text; retain type, never message.
        logger.debug("Subtitle parse failed: file=%s error=%s", path, type(e).__name__)
        raise SetupError(f"Cannot parse subtitle file '{path.name}': {e}") from e
    _raise_if_cancelled(cancel_check)

    units: list[ReadingUnit] = []
    for event in subs:
        _raise_if_cancelled(cancel_check)
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

    doc = ReadingDocument(
        title=path.stem,
        kind="subtitle",
        series=path.parent.name,
        episode=path.stem,
        units=units,
    )
    log_summary(
        logger,
        "Subtitle parse",
        file=path,
        cues=len(subs),
        units=len(units),
        skipped=len(subs) - len(units),
    )
    return doc

"""Secondary-language subtitle track: match its cues to a mined sentence window.

The mining-language subtitle is the primary track: it decides which words
are mined and what each card's sentence is. A second file in another
language (usually the user's own) rides beside it on the SAME video
timeline: shown under the primary cue in the curator preview, and written
to the card's Translation field. Nothing here parses or does I/O -- callers
hand in ``(start, end, text)`` cue tuples from
``SubtitleParserService.parse_raw_entries`` parsed at a ZERO offset, and the
second track's own offset is applied here, so the processor (card text) and
the curator (Translation column) run one rule on the same inputs and cannot
disagree.

Two independently timed tracks never line up exactly, so the join is by
overlap, not equality: every secondary cue overlapping the primary window by
at least ``min_overlap`` seconds attaches, in time order, joined by a single
space -- one primary line often spans two translated ones, and keeping only
the longest would lose half the sentence. A cue that merely touches the
window's edge (the normal case for neighbouring lines) does not attach.

When the feature is on and the user chose no file, :func:`load_auto_translation`
picks one (F-04): a subtitle named for the video, or a text track inside it,
tagged with the interface language or else English. That half does I/O (a
folder scan, ffprobe, an ffmpeg extract) and is called by the processor only.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SubtitleParseError
from anki_miner.languages.registry import available_languages, config_language, get_profile, subtitle_language
from anki_miner.models.word import TokenizedWord
from anki_miner.services.word_filter import CUE_JOINER
from anki_miner.utils.audio_track_detector import (
    SubtitleStream,
    TrackTitle,
    list_subtitle_streams,
    matches_language_tag,
    subtitle_stream_title,
)
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from anki_miner.utils.file_pairing import find_translation_sidecars

__all__ = [
    "DEFAULT_MIN_OVERLAP",
    "AutoTranslation",
    "RawEntryParser",
    "TranslationCandidate",
    "attach_translations",
    "load_auto_translation",
    "match_secondary_line",
    "rank_translation_candidates",
    "translation_languages",
]

logger = logging.getLogger(__name__)

#: Seconds a secondary cue must share with the primary window to count as
#: the same moment. Below it, an edge touch between neighbouring lines
#: attaches the wrong translation; above it, a genuinely short shared line
#: is lost. Capped at the window's own length, so a very short primary line
#: can still be matched.
DEFAULT_MIN_OVERLAP = 0.2


def match_secondary_line(
    entries: Sequence[tuple[float, float, str]],
    start: float,
    end: float,
    *,
    offset: float = 0.0,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
) -> str:
    """Text of every secondary cue overlapping ``[start, end]``, in time order.

    ``entries`` are raw-timeline cues; ``offset`` shifts them onto the video
    timeline the way ``parse_raw_entries`` and the player do
    (``max(0, t + offset)``).
    """
    if not entries or end <= start:
        return ""
    threshold = min(min_overlap, end - start)
    hits: list[tuple[float, str]] = []
    for cue_start, cue_end, text in entries:
        shifted_start = max(0.0, cue_start + offset)
        shifted_end = max(0.0, cue_end + offset)
        overlap = min(end, shifted_end) - max(start, shifted_start)
        if overlap <= 0.0 or overlap < threshold:
            continue
        hits.append((shifted_start, text))
    hits.sort(key=lambda hit: hit[0])
    return CUE_JOINER.join(text for _start, text in hits)


def attach_translations(
    words: list[TokenizedWord],
    entries: Sequence[tuple[float, float, str]],
    *,
    offset: float = 0.0,
) -> None:
    """Set ``sentence_translation`` on every word from its own sentence window.

    Mutates in place, like ``WordFilterService.attach_occurrence_counts``.
    Overwrites unconditionally: run it AFTER anything that moves a word's
    window (a curator sentence pick, a line expansion) and the text follows.
    """
    for word in words:
        word.sentence_translation = match_secondary_line(entries, word.start_time, word.end_time, offset=offset)


class RawEntryParser(Protocol):
    """The one parser method the auto-pick needs (``SubtitleParserService.parse_raw_entries``)."""

    def parse_raw_entries(
        self,
        subtitle_file: Path,
        subtitle_offset: float | None = None,
        *,
        encodings: tuple[str, ...] | None = None,
    ) -> list[tuple[float, float, str]]: ...


def translation_languages(ui_language: str) -> tuple[frozenset[str], ...]:
    """The tag sets a translation may carry, best first: the interface language, then English (F-04).

    ``ui_language`` is ``config.ui_language`` (``en``, ``de``, ``pt_br``). Its
    primary subtag picks the mining profile whose ``audio_track_codes`` name
    that language (``de`` answers to de/deu/ger/german); an interface language
    that is not a mining language answers to its bare code. English alone when
    the interface is English. Owner answer: "UI language, then English".
    """
    english = get_profile("en").audio_track_codes
    primary = ui_language.replace("_", "-").partition("-")[0].strip().lower() or "en"
    ui_codes = get_profile(primary).audio_track_codes if primary in available_languages() else frozenset({primary})
    return (english,) if ui_codes == english else (ui_codes, english)


@dataclass(frozen=True)
class TranslationCandidate:
    """One possible translation track: a sidecar file or a text stream inside the video."""

    #: Index of the language it matched in :func:`translation_languages` (0 = interface language).
    preference: int
    title: TrackTitle
    sidecar: Path | None = None
    stream: SubtitleStream | None = None

    @property
    def label(self) -> str:
        """The file name, or ``<n> [<lang>] <title>`` for a stream (n counts subtitle tracks from 1)."""
        if self.sidecar is not None:
            return self.sidecar.name
        if self.stream is None:
            return ""
        parts = [str(self.stream.sub_index + 1)]
        if self.stream.language_tag:
            parts.append(f"[{self.stream.language_tag}]")
        if self.stream.title:
            parts.append(self.stream.title)
        return " ".join(parts)


def rank_translation_candidates(candidates: Sequence[TranslationCandidate]) -> list[TranslationCandidate]:
    """Usable candidates, best first (F-04).

    Signs, songs, forced and commentary tracks are dropped: they are not the
    dialogue. The rest sort by language first (interface language, then
    English), then title (full dialogue, then a plain title, then SDH), then a
    sidecar before a stream, then the order they were found in (stream order).
    Language leads because a full track in a language the learner cannot read
    is worse than an untitled one they can.
    """
    usable = [c for c in candidates if c.title is not TrackTitle.PARTIAL]
    return sorted(usable, key=lambda c: (c.preference, int(c.title), c.stream is not None))


@dataclass(frozen=True)
class AutoTranslation:
    """The track :func:`load_auto_translation` picked, and its cues at a ZERO offset."""

    candidate: TranslationCandidate
    entries: list[tuple[float, float, str]]


def _embedded_candidates(
    video: Path, mining: frozenset[str], preferred: Sequence[frozenset[str]], ffprobe_cmd: str
) -> list[TranslationCandidate]:
    """*video*'s text streams tagged with a preferred, non-mining language. Untagged ones may be the mining track."""
    found: list[TranslationCandidate] = []
    for stream in list_subtitle_streams(video, ffprobe_cmd):
        if not stream.is_text or matches_language_tag(stream.language_tag, mining):
            continue
        preference = next(
            (i for i, codes in enumerate(preferred) if matches_language_tag(stream.language_tag, codes)), None
        )
        if preference is not None:
            found.append(TranslationCandidate(preference, subtitle_stream_title(stream), stream=stream))
    return found


def _parse(parser: RawEntryParser, path: Path, label: str) -> list[tuple[float, float, str]]:
    """Cues of *path* decoded like a chosen second file (BOM, then charset detection); [] when it will not parse."""
    try:
        return parser.parse_raw_entries(path, 0.0, encodings=())
    except SubtitleParseError as exc:
        logger.warning("secondary subtitle: skipped %s, it did not parse: %s", label, exc)
        return []


def _read_candidate(
    video: Path, candidate: TranslationCandidate, config: AnkiMinerConfig, parser: RawEntryParser, temp_dir: Path
) -> list[tuple[float, float, str]]:
    if candidate.sidecar is not None:
        return _parse(parser, candidate.sidecar, candidate.label)
    if candidate.stream is None:
        return []
    # audio_condenser brings the media stack with it; only an embedded pick needs it.
    from anki_miner.services.audio_condenser import AudioCondenserService

    temp_dir.mkdir(parents=True, exist_ok=True)
    extracted = AudioCondenserService(config).extract_embedded_subtitle(video, candidate.stream, temp_dir)
    if extracted is None:
        logger.warning("secondary subtitle: could not extract track %s from %s", candidate.label, video.name)
        return []
    try:
        return _parse(parser, extracted, candidate.label)
    finally:
        with contextlib.suppress(OSError):
            extracted.unlink()


def load_auto_translation(
    video: Path, config: AnkiMinerConfig, parser: RawEntryParser, temp_dir: Path
) -> AutoTranslation | None:
    """Pick and read a translation track for *video* when the user chose none (F-04).

    Candidates: subtitles named for the video and tagged for the interface
    language or English (:func:`find_translation_sidecars`), and the video's
    text streams tagged with one of those languages. Ranked by
    :func:`rank_translation_candidates`; the first that reads into at least one
    cue wins. A stream is extracted into *temp_dir* and deleted after parsing.
    A bad candidate is logged and skipped, never raised. ``None`` when nothing fits.
    """
    mining = subtitle_language(config_language(config))
    preferred = translation_languages(config.ui_language)
    candidates = [
        TranslationCandidate(s.preference, s.title, sidecar=s.path)
        for s in find_translation_sidecars(video, mining, preferred)
    ]
    candidates += _embedded_candidates(video, mining.mining, preferred, resolve_ffprobe(config))
    for candidate in rank_translation_candidates(candidates):
        entries = _read_candidate(video, candidate, config, parser, temp_dir)
        if entries:
            return AutoTranslation(candidate, entries)
    return None

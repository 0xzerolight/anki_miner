"""The sources a run reads besides a video: reading sources, audio files, a video's subtitle tracks.

``open_reading`` loads a book, text, subtitle file, manga volume or web article
exactly as Reading does (``load_reading_source``), with the parser the caller
passes: a run passes its processor's, so the units are the ones it mines;
``inspect``, ``media`` and ``sync`` pass ``create_line_parser``, the same
language's parser without its tokenizer.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from anki_miner.cli.api.contract import (
    AUDIO_UNREADABLE,
    BAD_RUN_FILE,
    CANCELLED,
    SOURCE_UNREADABLE,
    TRACK_NOT_FOUND,
    ApiError,
)
from anki_miner.cli.api.jobs import RunFailed
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.models.reading import ReadingDocument, ReadingSourceRef
from anki_miner.services.mokuro_volumes import mokuro_output_path
from anki_miner.services.reading import detector
from anki_miner.services.reading.web_article import ArticleFetchError, fetch_article
from anki_miner.services.track_extractor import probe_tracks
from anki_miner.utils.audio_track_detector import SubtitleStream, get_media_duration_seconds
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe

if TYPE_CHECKING:
    from anki_miner.gui.utils.service_factory import Services
    from anki_miner.services.subtitle_parser import SubtitleParserService

#: A ``reading_file``: a path, or a web address (a ``str``) whose article is fetched.
ReadingInput = Path | str

NEEDS_OCR = "{name} has no OCR text yet (no .mokuro beside or inside it). Run ocr on it first."
_SEVERAL = "{name} holds {count} manga volumes; name one of them (its .mokuro, archive or page folder)."
_NOT_READING = (
    "{name} is not a reading source. Supported: .epub, .txt, .pdf, subtitle files "
    "(.srt/.ass/.ssa/.vtt/.smi/.lrc), a manga volume (.mokuro, .cbz/.zip, a page folder) or a web address."
)


def reading_kind(path: Path) -> Literal["manga", "book", "subtitle"]:
    """What *path* is read as; BAD_RUN_FILE for a file that is no reading source."""
    kind = detector.source_kind(path)
    if kind is None:
        raise ApiError(BAD_RUN_FILE, _NOT_READING.format(name=path.name))
    return kind


def needs_ocr(path: Path) -> bool:
    """A manga volume (an archive or a page folder) that has no ``.mokuro`` yet."""
    if path.suffix.lower() == ".mokuro" or detector.source_kind(path) != "manga":
        return False
    try:
        detector.detect(path)
    except (SetupError, OSError, ValueError, zipfile.BadZipFile):
        return path.exists() and not mokuro_output_path(path).is_file()
    return False


def reading_ref(path: Path) -> ReadingSourceRef:
    """The one source *path* names: SOURCE_UNREADABLE when it does not load, or holds several volumes."""
    reading_kind(path)
    if not path.exists():
        raise ApiError(SOURCE_UNREADABLE, f"File not found: {path}")
    try:
        refs = detector.detect(path)
    except (SetupError, OSError, ValueError, zipfile.BadZipFile) as exc:
        if needs_ocr(path):
            raise ApiError(SOURCE_UNREADABLE, NEEDS_OCR.format(name=path.name)) from exc
        raise ApiError(SOURCE_UNREADABLE, str(exc) or type(exc).__name__) from exc
    if len(refs) != 1:
        raise ApiError(SOURCE_UNREADABLE, _SEVERAL.format(name=path.name, count=len(refs)))
    return refs[0]


@dataclass(frozen=True)
class _ParserOnly:
    """What ``load_reading_source`` reads off a processor for every kind but an Anki deck."""

    subtitle_parser: SubtitleParserService


def open_reading(
    config: AnkiMinerConfig,
    parser: SubtitleParserService,
    source: ReadingInput,
    *,
    cancel_check: Callable[[], bool],
) -> ReadingDocument:
    """*source* loaded as Reading loads it, through *parser*'s text seams.

    SOURCE_UNREADABLE when it does not load (a web article's network failure is
    transient), CANCELLED when *cancel_check* stops the load.
    """
    from anki_miner.gui.workers.reading_queue_worker import load_reading_source

    if isinstance(source, str):  # a web address: fetched, then read as pasted text (as `mine reading` does)
        try:
            article = fetch_article(source, rules=get_profile(config_language(config)).sentence_rules)
        except ArticleFetchError as exc:
            raise RunFailed(SOURCE_UNREADABLE, str(exc), transient=exc.transient) from exc
        ref = ReadingSourceRef(kind="text", title=article.source_label, text=article.text)
    else:
        ref = reading_ref(source)
    try:
        # Services is the other type load_reading_source takes; only subtitle_parser is read for these kinds.
        holder = cast("Services", _ParserOnly(parser))
        return load_reading_source(holder, config, ref, cancel_check=cancel_check)
    except OperationCancelled as exc:  # a SetupError: caught first
        raise ApiError(CANCELLED, "The run was cancelled.") from exc
    except (SetupError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ApiError(SOURCE_UNREADABLE, str(exc) or type(exc).__name__) from exc


def check_audio(config: AnkiMinerConfig, audio_file: Path) -> None:
    """AUDIO_UNREADABLE unless ffprobe finds a duration or an audio stream in *audio_file*."""
    ffprobe = resolve_ffprobe(config)
    if get_media_duration_seconds(audio_file, ffprobe) is None and not probe_tracks(audio_file, ffprobe).audio:
        raise ApiError(AUDIO_UNREADABLE, f"The audio file cannot be opened: {audio_file}")


def resolve_track(config: AnkiMinerConfig, video_file: Path, index: int) -> SubtitleStream:
    """The subtitle track whose ffprobe stream index is *index* (``inspect`` lists them); else TRACK_NOT_FOUND."""
    tracks = probe_tracks(video_file, resolve_ffprobe(config))
    found = next((stream for stream in tracks.subtitles if stream.index == index), None)
    if found is not None:
        return found
    if any(stream.global_index == index for stream in tracks.audio):
        raise ApiError(TRACK_NOT_FOUND, f"Track {index} of {video_file.name} is an audio track, not a subtitle.")
    raise ApiError(TRACK_NOT_FOUND, f"{video_file.name} has no subtitle track {index}. inspect lists its tracks.")

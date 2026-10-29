"""inspect: what Anki Miner makes of each source, before any long job (API.md, "inspect").

Per source: its kind, names, cover, unit count, chapter map, what it still
needs and its load warnings; for an audio file or a video, its length and
tracks. A reading source loads as mine loads it (``open_reading`` with the
settings an API run uses), so its units and their indexes are the ones mine's
``unit_index`` names; ``units: true`` writes them to
``<run_dir>/<run_id>/inspect-<n>.json``. The answer goes in the verdict's
``result``, a failing source carrying its own code. No Anki and no lock:
nothing is written but those unit lists.
"""

from __future__ import annotations

import functools
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from anki_miner.cli.api import files, settings, sources
from anki_miner.cli.api.contract import BAD_RUN_FILE, CANCELLED, VIDEO_UNREADABLE, ApiError
from anki_miner.cli.api.ocr import page_images
from anki_miner.cli.api.runfolder import next_numbered, write_json
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.constants import AUDIO_EXTENSIONS
from anki_miner.gui.utils.service_factory import create_line_parser
from anki_miner.models.reading import ReadingDocument
from anki_miner.services.mokuro_volumes import mokuro_output_path
from anki_miner.services.reading.detector import source_kind
from anki_miner.services.reading.web_article import is_web_url
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.services.track_extractor import TRACKS_VIDEO_EXTENSIONS, probe_tracks
from anki_miner.utils.audio_track_detector import get_media_duration_seconds, get_primary_video_codec
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe

#: A book loader's label where the book names no chapter: an EPUB's ch.N, a text's ¶N, a PDF's p.N.
_NO_CHAPTER = re.compile(r"(?:ch\.|¶|p\.)\d+")
_UNKNOWN = (
    "{name} is not a source inspect reads: a book, text, subtitle file, manga volume or web address, "
    "an audio file (.m4b, .mp3, .m4a, .aac, .ogg, .opus, .flac, .wav) or a video."
)
#: A source's keys, in order; a source that fails has only path, error and message set.
_KEYS = (
    "path",
    "kind",
    "title",
    "series",
    "episode",
    "cover",
    "units",
    "chapters",
    "needs",
    "warnings",
    "duration",
    "tracks",
    "file",
    "error",
    "message",
)


@dataclass(frozen=True)
class SourceRequest:
    #: A web address stays a str.
    path: Path | str
    units: bool
    run_id: str | None


@dataclass(frozen=True)
class InspectFile:
    profile: str | None
    language: object  # validated by settings.with_language
    run_dir: Path | None
    sources: tuple[SourceRequest, ...]


def _source_request(raw: object, where: str) -> SourceRequest:
    obj = files.json_object(raw, where)
    files.check_keys(obj, frozenset({"path", "units", "run_id"}), frozenset({"path"}), where)
    path = files.text(obj["path"], f"{where}.path")
    if not path.strip():
        raise files.bad(f"{where}.path is empty.")
    units = obj.get("units", False)
    if not isinstance(units, bool):
        raise files.bad(f"{where}.units must be true or false.")
    run_id = None if obj.get("run_id") is None else files.valid_run_id(obj["run_id"], f"{where}.run_id")
    if units and run_id is None:
        raise files.bad(f"{where}.run_id is required with units: the unit list goes to <run_dir>/<run_id>/.")
    return SourceRequest(path if is_web_url(path) else files.abs_path(path), units, run_id)


def parse_inspect_file(data: object) -> InspectFile:
    """``schema``, ``language``, ``profile``, ``run_dir`` (needed only for a unit list) and ``sources``."""
    where = "The inspect file"
    obj = files.json_object(data, where)
    files.check_keys(
        obj,
        frozenset({"schema", "language", "profile", "run_dir", "sources"}),
        frozenset({"schema", "language", "sources"}),
        where,
    )
    files._schema(obj, where)
    raw_sources = obj["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise files.bad("sources must be a non-empty list.")
    requests = tuple(_source_request(raw, f"sources[{i}]") for i, raw in enumerate(raw_sources))
    run_dir = None if obj.get("run_dir") is None else files._run_dir(obj["run_dir"])
    if run_dir is None and any(request.units for request in requests):
        raise files.bad("run_dir is required when a source asks for its units.")
    return InspectFile(files.opt_text(obj.get("profile"), "profile"), obj["language"], run_dir, requests)


def call(inspect_file: Path, cancel: threading.Event) -> dict[str, object]:
    """``inspect INSPECT_FILE``: the file checked, then each source in order; CANCELLED stops the call."""
    job = parse_inspect_file(files.read_json_file(inspect_file))
    # The settings mine runs a reading source with (subtitle lines it keeps, say): the same units.
    config = settings.resolve_run_config(job.profile, job.language, {})
    parser = functools.cache(lambda: create_line_parser(config))
    return {"sources": [_inspect(config, parser, job.run_dir, request, cancel) for request in job.sources]}


def _inspect(
    config: AnkiMinerConfig,
    parser: Callable[[], SubtitleParserService],
    run_dir: Path | None,
    request: SourceRequest,
    cancel: threading.Event,
) -> dict[str, object]:
    if cancel.is_set():
        raise ApiError(CANCELLED, "inspect was cancelled.")
    try:
        entry, document = _describe(config, parser, request, cancel)
    except ApiError as exc:
        if exc.code == CANCELLED:
            raise
        return {**dict.fromkeys(_KEYS), "path": str(request.path), "error": exc.code, "message": exc.message}
    if request.units and document is not None:
        assert run_dir is not None and request.run_id is not None  # parse_inspect_file
        entry["file"] = _write_units(run_dir, request.run_id, document)
    return entry


def _describe(
    config: AnkiMinerConfig,
    parser: Callable[[], SubtitleParserService],
    request: SourceRequest,
    cancel: threading.Event,
) -> tuple[dict[str, object], ReadingDocument | None]:
    """A web address or a reading file by its name, audio by its extension, a manga volume mokuro has
    not read yet (page images and no .mokuro), else a video when ffprobe finds one in it."""
    path = request.path
    if isinstance(path, Path):
        if path.suffix.lower() in AUDIO_EXTENSIONS:
            return _audio(config, path), None
        if source_kind(path) is None:
            return _video(config, path), None
        if sources.needs_ocr(path) and page_images(path) > 0:
            return _owed_volume(path), None
    document = sources.open_reading(config, parser(), path, cancel_check=cancel.is_set)
    return _reading(str(path), document), document


def _entry(path: str, kind: str, title: str | None, series: str | None, episode: str | None) -> dict[str, object]:
    return {
        **dict.fromkeys(_KEYS),
        "path": path,
        "kind": kind,
        "title": title,
        "series": series,
        "episode": episode or None,
        "cover": False,
        "chapters": [],
        "needs": [],
        "warnings": [],
        "tracks": [],
    }


def _reading(path: str, document: ReadingDocument) -> dict[str, object]:
    entry = _entry(path, document.kind, document.title, document.series, document.episode)
    # A book's cover and a manga volume's pages ride on its units as their picture.
    entry["cover"] = any(unit.image_ref is not None for unit in document.units)
    entry["units"] = len(document.units)
    entry["chapters"] = _chapters(document) if document.kind == "book" else []
    entry["warnings"] = list(document.warnings)
    return entry


def _chapters(document: ReadingDocument) -> list[dict[str, object]]:
    """Each run of one location label, at its first unit; the loaders' fallback labels name no chapter."""
    chapters: list[dict[str, object]] = []
    previous = None
    for unit in document.units:
        label = unit.location_label
        if label != previous and label and not _NO_CHAPTER.fullmatch(label):
            chapters.append({"title": label, "index": unit.index})
        previous = label
    return chapters


def _owed_volume(path: Path) -> dict[str, object]:
    """A volume mokuro has not read: named as its .mokuro will name it (its folder, its own name)."""
    mokuro_file = mokuro_output_path(path)
    entry = _entry(str(path), "manga", mokuro_file.parent.name, mokuro_file.parent.name, mokuro_file.stem)
    entry["cover"] = True  # its pages
    entry["units"] = 0
    entry["needs"] = ["ocr"]
    return entry


def _audio(config: AnkiMinerConfig, path: Path) -> dict[str, object]:
    """Named as an audio episode's cards are (the Audiobooks tab: series "Audio", the file's name)."""
    sources.check_audio(config, path)
    ffprobe = resolve_ffprobe(config)
    entry = _entry(str(path), "audio", path.stem, "Audio", path.stem)
    # An audio file's picture is a video stream (attached_pic), the one an audio card takes.
    entry["cover"] = get_primary_video_codec(path, ffprobe) is not None
    entry["duration"] = _duration(path, ffprobe)
    entry["tracks"] = _tracks(path, ffprobe)
    return entry


def _video(config: AnkiMinerConfig, path: Path) -> dict[str, object]:
    """Named as mine names a video episode (its folder, its own name)."""
    ffprobe = resolve_ffprobe(config)
    if get_primary_video_codec(path, ffprobe) is None:
        if path.suffix.lower() in TRACKS_VIDEO_EXTENSIONS:
            raise ApiError(VIDEO_UNREADABLE, f"The video cannot be opened: {path}")
        raise ApiError(BAD_RUN_FILE, _UNKNOWN.format(name=path.name))
    entry = _entry(str(path), "video", path.stem, path.parent.name, path.stem)
    entry["duration"] = _duration(path, ffprobe)
    entry["tracks"] = _tracks(path, ffprobe)
    return entry


def _duration(path: Path, ffprobe: str) -> float | None:
    seconds = get_media_duration_seconds(path, ffprobe)
    return None if seconds is None else round(seconds, 3)


def _tracks(path: Path, ffprobe: str) -> list[dict[str, object]]:
    """Subtitle and audio tracks by ffprobe stream index; ``position`` counts within the kind
    (a subtitle's ``-map 0:s:N``, an audio track's ``audio_track_override``)."""
    found = probe_tracks(path, ffprobe)
    rows: list[tuple[int, dict[str, object]]] = [
        (
            s.index,
            {
                "index": s.index,
                "position": s.sub_index,
                "kind": "subtitle",
                "codec": s.codec_name,
                "language": s.language_tag,
                "title": s.title,
                "default": s.is_default,
                "forced": s.is_forced,
                "text": s.is_text,
            },
        )
        for s in found.subtitles
    ]
    rows += [
        (
            a.global_index,
            {
                "index": a.global_index,
                "position": a.audio_index,
                "kind": "audio",
                "codec": a.codec,
                "language": a.language_tag,
                "title": a.title_tag,
                "default": a.is_default,
                "forced": None,  # ffprobe's flag is not read for audio
                "text": None,
            },
        )
        for a in found.audio
    ]
    return [row for _, row in sorted(rows, key=lambda pair: pair[0])]


def _write_units(run_dir: Path, run_id: str, document: ReadingDocument) -> str:
    folder = run_dir / run_id
    folder.mkdir(exist_ok=True)
    path = next_numbered(folder, "inspect")
    units = [{"index": unit.index, "location": unit.location_label, "text": unit.text} for unit in document.units]
    write_json(path, {"schema": 1, "run_id": run_id, "units": units})
    return path.name

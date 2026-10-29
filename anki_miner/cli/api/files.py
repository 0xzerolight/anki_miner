"""The run, media and job files, validated into typed objects (API.md).

The field readers (``text``, ``abs_path``, ``valid_run_id``, …) are shared with
the job commands, which parse their own episodes through :func:`parse_jobs_file`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from anki_miner.cli.api.contract import BAD_RUN_FILE, ApiError
from anki_miner.services.reading.detector import source_kind
from anki_miner.services.reading.web_article import is_web_url
from anki_miner.utils.bounded_reader import read_json_bounded
from anki_miner.utils.youtube_url import classify_youtube_url

MAX_FILE_BYTES = 8 * 1024 * 1024
_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
#: Names Windows keeps for devices, in any case and with any extension (a run_id has none): no folder can have one.
_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"{port}{n}" for port in ("COM", "LPT") for n in range(1, 10))}
)
_UNREADABLE = object()

_EPISODE_KEYS = frozenset(
    {
        "run_id",
        "video_file",
        "audio_file",
        "reading_file",
        "subtitle_file",
        "subtitle_offset",
        "audio_track_override",
        "source_label_override",
        "secondary_subtitle_file",
        "secondary_subtitle_offset",
        "series_name_override",
        "episode_name_override",
        "tags",
        "words",
    }
)
_EPISODE_REQUIRED = frozenset({"run_id", "words"})
_WORD_KEYS = frozenset({"word", "line_start", "line_text", "line_expansion", "surface", "reading", "unit_index"})
_MEDIA_EPISODE_KEYS = frozenset(
    {
        "run_id",
        "video_file",
        "audio_file",
        "reading_file",
        "subtitle_file",
        "subtitle_offset",
        "audio_track_override",
        "lines",
    }
)
#: An episode's source: exactly one of them.
_SOURCE_KEYS = ("video_file", "audio_file", "reading_file")
#: What a reading episode has none of: a subtitle file, its timeline, an audio track or a translation track.
_NOT_FOR_READING = (
    "subtitle_file",
    "subtitle_offset",
    "audio_track_override",
    "secondary_subtitle_file",
    "secondary_subtitle_offset",
)
_NOT_READING = (
    "{where}.reading_file is not a reading source: {name}. Supported: .epub, .txt, .pdf, subtitle files "
    "(.srt/.ass/.ssa/.vtt/.smi/.lrc), a manga volume (.mokuro, .cbz/.zip, a page folder) or a web address."
)
_FETCH_EPISODE_KEYS = frozenset({"run_id", "youtube_url", "youtube_subtitle_source", "youtube_align_captions"})
_SUBTITLE_SOURCES = ("auto", "captions", "transcribe")


def bad(message: str) -> ApiError:
    return ApiError(BAD_RUN_FILE, message)


def read_json_file(path: Path) -> object:
    data = read_json_bounded(path, MAX_FILE_BYTES, _UNREADABLE, "API input")
    if data is _UNREADABLE:
        raise bad(f"{path} cannot be read, or is not JSON.")
    return data


def json_object(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise bad(f"{where} must be a JSON object.")
    return value


def check_keys(obj: Mapping[str, object], allowed: frozenset[str], required: frozenset[str], where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise bad(f"{where} has unknown keys: {', '.join(unknown)}")
    missing = sorted(required - set(obj))
    if missing:
        raise bad(f"{where} is missing: {', '.join(missing)}")


def text(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise bad(f"{where} must be a string.")
    # json.loads accepts a lone "\ud83d" escape (a string cut inside an emoji),
    # but every file this run writes is UTF-8: refuse it now, before anything is
    # mined, not after the notes are added and the result cannot be written.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise bad(f"{where} is not valid text (it holds an unpaired surrogate).") from None
    return value


def opt_text(value: object, where: str) -> str | None:
    return None if value is None else text(value, where)


def opt_int(value: object, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise bad(f"{where} must be a whole number.")
    return value


def opt_float(value: object, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise bad(f"{where} must be a number.")
    try:
        number = float(value)
    except OverflowError:  # an int past float range
        number = math.inf
    if not math.isfinite(number):
        raise bad(f"{where} must be a finite number.")
    return number


def _schema(obj: Mapping[str, object], where: str) -> None:
    schema = obj.get("schema")
    if isinstance(schema, bool) or schema != 1:
        raise bad(f"{where}: schema must be 1.")


def _run_dir(value: object) -> Path:
    # Resolved, so a relative run_dir is taken from the caller's folder.
    path = Path(text(value, "run_dir")).expanduser().resolve()
    if not path.is_dir():
        raise bad(f"run_dir must be an existing folder: {path}")
    return path


def valid_run_id(value: object, where: str) -> str:
    run_id = text(value, where)
    if not _RUN_ID.fullmatch(run_id):
        raise bad(f"{where} may hold only letters, digits, '-' and '_' (at most 64).")
    if run_id.upper() in _DEVICE_NAMES:
        raise bad(f"{where} is a Windows device name, which cannot name a folder: {run_id}")
    return run_id


def _unique_run_ids(run_ids: list[str]) -> None:
    """Each episode its own folder: run_ids differing only in letter case share one on Windows and macOS."""
    if len({run_id.casefold() for run_id in run_ids}) != len(run_ids):
        raise bad("Two episodes share a run_id (letter case aside).")


def abs_path(value: str) -> Path:
    """An input path made absolute against the caller's working folder."""
    return Path(value).expanduser().resolve()


def opt_path(value: str | None) -> Path | None:
    return abs_path(value) if value is not None else None


@dataclass(frozen=True)
class WordRequest:
    word: str
    line_start: float | None = None
    line_text: str | None = None
    line_expansion: tuple[int, int] | None = None
    surface: str | None = None
    reading: str | None = None
    #: A reading_file's unit, by its index (``inspect`` lists them)
    unit_index: int | None = None


def _word_request(raw: object, where: str, *, reading_episode: bool = False) -> WordRequest:
    """One word; on a reading episode it names its unit by ``unit_index``, never by ``line_start``."""
    obj = json_object(raw, where)
    check_keys(obj, _WORD_KEYS, frozenset({"word"}), where)
    word = text(obj["word"], f"{where}.word")
    if not word.strip():
        raise bad(f"{where}.word is empty.")
    line_start = opt_float(obj.get("line_start"), f"{where}.line_start")
    if line_start is not None and line_start < 0:
        raise bad(f"{where}.line_start cannot be negative.")
    if line_start is not None and reading_episode:
        raise bad(f"{where}.line_start names a subtitle line: a reading_file's words name a unit_index or line_text.")
    unit_index = _unit_index(obj.get("unit_index"), where, reading_episode=reading_episode)
    line_text = opt_text(obj.get("line_text"), f"{where}.line_text")
    if line_text is not None and not line_text.strip():
        raise bad(f"{where}.line_text is empty.")
    expansion = parse_expansion(obj.get("line_expansion"), where)
    surface = opt_text(obj.get("surface"), f"{where}.surface")
    reading = opt_text(obj.get("reading"), f"{where}.reading")
    for key, value in (("surface", surface), ("reading", reading)):
        if value is not None and not value.strip():
            raise bad(f"{where}.{key} is empty.")
    return WordRequest(word, line_start, line_text, expansion, surface, reading, unit_index)


def _unit_index(raw: object, where: str, *, reading_episode: bool) -> int | None:
    """A ``unit_index``: a whole number of 0 or more, on a reading episode only."""
    index = opt_int(raw, f"{where}.unit_index")
    if index is None:
        return None
    if not reading_episode:
        raise bad(f"{where}.unit_index names a reading_file's unit: a subtitle line is named by line_start.")
    if index < 0:
        raise bad(f"{where}.unit_index cannot be negative.")
    return index


def parse_expansion(raw: object, where: str) -> tuple[int, int] | None:
    """A ``line_expansion``: ``[before, after]``, or None when left out."""
    if raw is None:
        return None
    if not (
        isinstance(raw, list)
        and len(raw) == 2
        and all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in raw)
    ):
        raise bad(f"{where}.line_expansion must be [before, after], two whole numbers of 0 or more.")
    return (raw[0], raw[1])


#: A ``reading_file``: a path, or a web address (a ``str``) whose article is fetched.
ReadingInput = Path | str


def _reading_file(value: object, where: str) -> ReadingInput:
    """A web address as written, else a path that names a reading source.

    A path is judged by its extension, and one without as a manga page folder
    when it is a folder; one that has neither and does not exist is left to the
    run, which reports it SOURCE_UNREADABLE as it does a missing file.
    """
    raw = text(value, f"{where}.reading_file")
    if is_web_url(raw):
        return raw.strip()
    path = abs_path(raw)
    if source_kind(path) is None and (path.suffix or path.exists()):
        raise bad(_NOT_READING.format(where=where, name=path.name))
    return path


@dataclass(frozen=True)
class _Source:
    """An episode's source: exactly one of the three files, and the subtitle file a video or audio file needs."""

    video_file: Path | None
    audio_file: Path | None
    reading_file: ReadingInput | None
    subtitle_file: Path | None


def _episode_source(obj: Mapping[str, object], where: str) -> _Source:
    """``video_file``, ``audio_file`` or ``reading_file``, and ``subtitle_file``.

    A reading source has no subtitle file, nor anything that times one or picks
    an audio track (``_NOT_FOR_READING``).
    """
    named = [key for key in _SOURCE_KEYS if obj.get(key) is not None]
    if len(named) != 1:
        raise bad(f"{where} must name exactly one of video_file, audio_file and reading_file.")
    [key] = named
    if key == "reading_file":
        refused = [k for k in _NOT_FOR_READING if obj.get(k) is not None]
        if refused:
            raise bad(f"{where} has a reading_file, which takes none of: {', '.join(refused)}")
        return _Source(None, None, _reading_file(obj[key], where), None)
    if obj.get("subtitle_file") is None:
        raise bad(f"{where} is missing: subtitle_file")
    path = abs_path(text(obj[key], f"{where}.{key}"))
    subtitle = abs_path(text(obj["subtitle_file"], f"{where}.subtitle_file"))
    if key == "audio_file":
        return _Source(None, path, None, subtitle)
    return _Source(path, None, None, subtitle)


@dataclass(frozen=True)
class Episode:
    run_id: str
    #: Exactly one of video_file, audio_file and reading_file is set.
    video_file: Path | None
    #: None on a reading episode only.
    subtitle_file: Path | None
    subtitle_offset: float
    audio_track_override: int | None
    source_label_override: str | None
    secondary_subtitle_file: Path | None
    secondary_subtitle_offset: float
    series_name_override: str | None
    episode_name_override: str | None
    tags: str
    words: tuple[WordRequest, ...]
    audio_file: Path | None = None
    reading_file: ReadingInput | None = None

    @property
    def media_file(self) -> Path | None:
        """The video or audio file the clips are cut from; None on a reading episode."""
        return self.video_file or self.audio_file

    def process_kwargs(self) -> dict[str, object]:
        """The optional ``process_episode`` arguments this episode sets.

        An audio episode mines as the Audiobooks tab does: audio only (the clip,
        and the file's own cover as its picture), its cards named ``Audio`` and
        the file's stem unless the episode names them.
        """
        audio = self.audio_file
        series, episode = self.series_name_override, self.episode_name_override
        return {
            "subtitle_offset": self.subtitle_offset,
            "audio_track_override": self.audio_track_override,
            "source_label_override": self.source_label_override,
            "secondary_subtitle_file": self.secondary_subtitle_file,
            "secondary_subtitle_offset": self.secondary_subtitle_offset,
            "series_name_override": "Audio" if audio is not None and series is None else series,
            "episode_name_override": audio.stem if audio is not None and episode is None else episode,
            "audio_only": audio is not None,
        }

    def reading_kwargs(self) -> dict[str, str | None]:
        """The optional ``process_reading`` arguments this episode sets."""
        return {
            "source_label_override": self.source_label_override,
            "series_name_override": self.series_name_override,
            "episode_name_override": self.episode_name_override,
        }


def parse_episode(raw: object, where: str) -> Episode:
    obj = json_object(raw, where)
    check_keys(obj, _EPISODE_KEYS, _EPISODE_REQUIRED, where)
    source = _episode_source(obj, where)
    secondary = opt_path(opt_text(obj.get("secondary_subtitle_file"), f"{where}.secondary_subtitle_file"))
    raw_words = obj["words"]
    if not isinstance(raw_words, list) or not raw_words:
        raise bad(f"{where}.words must list at least one word (there is no 'all').")
    reading = source.reading_file is not None
    words = tuple(_word_request(w, f"{where}.words[{j}]", reading_episode=reading) for j, w in enumerate(raw_words))
    return Episode(
        run_id=valid_run_id(obj["run_id"], f"{where}.run_id"),
        video_file=source.video_file,
        subtitle_file=source.subtitle_file,
        subtitle_offset=opt_float(obj.get("subtitle_offset"), f"{where}.subtitle_offset") or 0.0,
        audio_track_override=opt_int(obj.get("audio_track_override"), f"{where}.audio_track_override"),
        source_label_override=opt_text(obj.get("source_label_override"), f"{where}.source_label_override"),
        secondary_subtitle_file=secondary,
        secondary_subtitle_offset=opt_float(obj.get("secondary_subtitle_offset"), f"{where}.secondary_subtitle_offset")
        or 0.0,
        series_name_override=opt_text(obj.get("series_name_override"), f"{where}.series_name_override"),
        episode_name_override=opt_text(obj.get("episode_name_override"), f"{where}.episode_name_override"),
        tags=opt_text(obj.get("tags"), f"{where}.tags") or "",
        words=words,
        audio_file=source.audio_file,
        reading_file=source.reading_file,
    )


@dataclass(frozen=True)
class RunFile:
    run_dir: Path
    profile: str | None
    language: object  # validated by settings.with_language
    overlay: Mapping[str, object]
    episodes: tuple[Episode, ...]
    dry_run: bool = False


def parse_run_file(data: object) -> RunFile:
    obj = json_object(data, "The run file")
    check_keys(
        obj,
        frozenset({"schema", "run_dir", "profile", "language", "config", "episodes", "dry_run"}),
        frozenset({"schema", "run_dir", "language", "episodes"}),
        "The run file",
    )
    _schema(obj, "The run file")
    dry_run = obj.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise bad("dry_run must be true or false.")
    raw_episodes = obj["episodes"]
    if not isinstance(raw_episodes, list) or not raw_episodes:
        raise bad("episodes must be a non-empty list.")
    episodes = tuple(parse_episode(raw, f"episodes[{i}]") for i, raw in enumerate(raw_episodes))
    _unique_run_ids([episode.run_id for episode in episodes])
    return RunFile(
        run_dir=_run_dir(obj["run_dir"]),
        profile=opt_text(obj.get("profile"), "profile"),
        language=obj["language"],
        overlay=json_object(obj.get("config", {}), "config"),
        episodes=episodes,
        dry_run=dry_run,
    )


@dataclass(frozen=True)
class LineRequest:
    #: a subtitle line's start (video and audio episodes)
    line_start: float | None = None
    line_expansion: tuple[int, int] | None = None
    #: a reading_file's unit (reading episodes)
    unit_index: int | None = None


@dataclass(frozen=True)
class MediaEpisode:
    run_id: str
    #: Exactly one of video_file, audio_file and reading_file is set.
    video_file: Path | None
    #: None on a reading episode only.
    subtitle_file: Path | None
    subtitle_offset: float
    audio_track_override: int | None
    lines: tuple[LineRequest, ...]
    audio_file: Path | None = None
    reading_file: ReadingInput | None = None


@dataclass(frozen=True)
class MediaFile:
    run_dir: Path
    profile: str | None
    language: object  # validated by settings.with_language
    still_height: int | None
    audio_bitrate: int | None
    episodes: tuple[MediaEpisode, ...]


def opt_positive(value: object, where: str) -> int | None:
    number = opt_int(value, where)
    if number is not None and number < 1:
        raise bad(f"{where} must be 1 or more.")
    return number


def _line_request(raw: object, where: str, *, reading: bool) -> LineRequest:
    """One line to cut: a ``unit_index`` on a reading episode, else a ``line_start``."""
    obj = json_object(raw, where)
    check_keys(obj, frozenset({"line_start", "unit_index", "line_expansion"}), frozenset(), where)
    expansion = parse_expansion(obj.get("line_expansion"), where)
    if reading:
        if obj.get("line_start") is not None:
            raise bad(f"{where}.line_start names a subtitle line: a reading_file's lines name a unit_index.")
        index = _unit_index(obj.get("unit_index"), where, reading_episode=True)
        if index is None:
            raise bad(f"{where} is missing: unit_index")
        return LineRequest(line_expansion=expansion, unit_index=index)
    _unit_index(obj.get("unit_index"), where, reading_episode=False)
    start = opt_float(obj.get("line_start"), f"{where}.line_start")
    if start is None or start < 0:
        raise bad(f"{where}.line_start must be a number of 0 or more.")
    return LineRequest(start, expansion)


def parse_media_file(data: object) -> MediaFile:
    obj = json_object(data, "The media file")
    check_keys(
        obj,
        frozenset({"schema", "run_dir", "profile", "language", "still_height", "audio_bitrate", "episodes"}),
        frozenset({"schema", "run_dir", "language", "episodes"}),
        "The media file",
    )
    _schema(obj, "The media file")
    raw_episodes = obj["episodes"]
    if not isinstance(raw_episodes, list) or not raw_episodes:
        raise bad("episodes must be a non-empty list.")
    episodes: list[MediaEpisode] = []
    for i, raw in enumerate(raw_episodes):
        where = f"episodes[{i}]"
        ep = json_object(raw, where)
        check_keys(ep, _MEDIA_EPISODE_KEYS, frozenset({"run_id", "lines"}), where)
        source = _episode_source(ep, where)
        raw_lines = ep["lines"]
        if not isinstance(raw_lines, list) or not raw_lines:
            raise bad(f"{where}.lines must list at least one line.")
        reading = source.reading_file is not None
        episodes.append(
            MediaEpisode(
                run_id=valid_run_id(ep["run_id"], f"{where}.run_id"),
                video_file=source.video_file,
                subtitle_file=source.subtitle_file,
                subtitle_offset=opt_float(ep.get("subtitle_offset"), f"{where}.subtitle_offset") or 0.0,
                audio_track_override=opt_int(ep.get("audio_track_override"), f"{where}.audio_track_override"),
                lines=tuple(
                    _line_request(line, f"{where}.lines[{j}]", reading=reading) for j, line in enumerate(raw_lines)
                ),
                audio_file=source.audio_file,
                reading_file=source.reading_file,
            )
        )
    _unique_run_ids([episode.run_id for episode in episodes])
    return MediaFile(
        run_dir=_run_dir(obj["run_dir"]),
        profile=opt_text(obj.get("profile"), "profile"),
        language=obj["language"],
        still_height=opt_positive(obj.get("still_height"), "still_height"),
        audio_bitrate=opt_positive(obj.get("audio_bitrate"), "audio_bitrate"),
        episodes=tuple(episodes),
    )


@dataclass(frozen=True)
class FetchEpisode:
    run_id: str
    youtube_url: str
    subtitle_source: str | None  # None: the profile's youtube_subtitle_source
    align_captions: bool | None  # None: the profile's youtube_align_captions


def _fetch_episode(raw: object, where: str) -> FetchEpisode:
    obj = json_object(raw, where)
    check_keys(obj, _FETCH_EPISODE_KEYS, frozenset({"run_id", "youtube_url"}), where)
    url = text(obj["youtube_url"], f"{where}.youtube_url")
    if classify_youtube_url(url).kind not in ("video", "video_in_playlist"):
        raise bad(f"{where}.youtube_url is not one YouTube video's link (playlist links are not supported).")
    source = opt_text(obj.get("youtube_subtitle_source"), f"{where}.youtube_subtitle_source")
    if source is not None and source not in _SUBTITLE_SOURCES:
        raise bad(f"{where}.youtube_subtitle_source must be one of {', '.join(_SUBTITLE_SOURCES)}.")
    align = obj.get("youtube_align_captions")
    if align is not None and not isinstance(align, bool):
        raise bad(f"{where}.youtube_align_captions must be true or false.")
    return FetchEpisode(valid_run_id(obj["run_id"], f"{where}.run_id"), url, source, align)


def parse_fetch_file(data: object) -> JobsFile[FetchEpisode]:
    return parse_jobs_file(data, "fetch", _fetch_episode)


class _HasRunId(Protocol):
    @property
    def run_id(self) -> str: ...


E = TypeVar("E", bound=_HasRunId)


@dataclass(frozen=True)
class JobsFile(Generic[E]):
    """A file-producing command's file (fetch, ocr, sync, …): the shared header and its episodes."""

    run_dir: Path
    profile: str | None
    language: object  # validated by settings.with_language
    episodes: tuple[E, ...]


def parse_jobs_file(data: object, name: str, episode: Callable[[object, str], E]) -> JobsFile[E]:
    """``schema``, ``run_dir``, ``profile``, ``language`` and ``episodes``, each parsed by *episode*."""
    where = f"The {name} file"
    obj = json_object(data, where)
    check_keys(
        obj,
        frozenset({"schema", "run_dir", "profile", "language", "episodes"}),
        frozenset({"schema", "run_dir", "language", "episodes"}),
        where,
    )
    _schema(obj, where)
    raw_episodes = obj["episodes"]
    if not isinstance(raw_episodes, list) or not raw_episodes:
        raise bad("episodes must be a non-empty list.")
    episodes = tuple(episode(raw, f"episodes[{i}]") for i, raw in enumerate(raw_episodes))
    _unique_run_ids([e.run_id for e in episodes])
    return JobsFile(
        run_dir=_run_dir(obj["run_dir"]),
        profile=opt_text(obj.get("profile"), "profile"),
        language=obj["language"],
        episodes=episodes,
    )

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
_EPISODE_REQUIRED = frozenset({"run_id", "video_file", "subtitle_file", "words"})
_WORD_KEYS = frozenset({"word", "line_start", "line_text", "line_expansion", "surface", "reading"})
_MEDIA_EPISODE_KEYS = frozenset(
    {"run_id", "video_file", "subtitle_file", "subtitle_offset", "audio_track_override", "lines"}
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


def _word_request(raw: object, where: str) -> WordRequest:
    obj = json_object(raw, where)
    check_keys(obj, _WORD_KEYS, frozenset({"word"}), where)
    word = text(obj["word"], f"{where}.word")
    if not word.strip():
        raise bad(f"{where}.word is empty.")
    line_start = opt_float(obj.get("line_start"), f"{where}.line_start")
    if line_start is not None and line_start < 0:
        raise bad(f"{where}.line_start cannot be negative.")
    line_text = opt_text(obj.get("line_text"), f"{where}.line_text")
    if line_text is not None and not line_text.strip():
        raise bad(f"{where}.line_text is empty.")
    expansion = parse_expansion(obj.get("line_expansion"), where)
    surface = opt_text(obj.get("surface"), f"{where}.surface")
    reading = opt_text(obj.get("reading"), f"{where}.reading")
    for key, value in (("surface", surface), ("reading", reading)):
        if value is not None and not value.strip():
            raise bad(f"{where}.{key} is empty.")
    return WordRequest(word, line_start, line_text, expansion, surface, reading)


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


@dataclass(frozen=True)
class Episode:
    run_id: str
    video_file: Path
    subtitle_file: Path
    subtitle_offset: float
    audio_track_override: int | None
    source_label_override: str | None
    secondary_subtitle_file: Path | None
    secondary_subtitle_offset: float
    series_name_override: str | None
    episode_name_override: str | None
    tags: str
    words: tuple[WordRequest, ...]

    def process_kwargs(self) -> dict[str, object]:
        """The optional ``process_episode`` arguments this episode sets."""
        return {
            "subtitle_offset": self.subtitle_offset,
            "audio_track_override": self.audio_track_override,
            "source_label_override": self.source_label_override,
            "secondary_subtitle_file": self.secondary_subtitle_file,
            "secondary_subtitle_offset": self.secondary_subtitle_offset,
            "series_name_override": self.series_name_override,
            "episode_name_override": self.episode_name_override,
        }


def parse_episode(raw: object, where: str) -> Episode:
    obj = json_object(raw, where)
    check_keys(obj, _EPISODE_KEYS, _EPISODE_REQUIRED, where)
    video_file = abs_path(text(obj["video_file"], f"{where}.video_file"))
    subtitle_file = abs_path(text(obj["subtitle_file"], f"{where}.subtitle_file"))
    secondary = opt_path(opt_text(obj.get("secondary_subtitle_file"), f"{where}.secondary_subtitle_file"))
    raw_words = obj["words"]
    if not isinstance(raw_words, list) or not raw_words:
        raise bad(f"{where}.words must list at least one word (there is no 'all').")
    words = tuple(_word_request(w, f"{where}.words[{j}]") for j, w in enumerate(raw_words))
    return Episode(
        run_id=valid_run_id(obj["run_id"], f"{where}.run_id"),
        video_file=video_file,
        subtitle_file=subtitle_file,
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
    line_start: float
    line_expansion: tuple[int, int] | None = None


@dataclass(frozen=True)
class MediaEpisode:
    run_id: str
    video_file: Path
    subtitle_file: Path
    subtitle_offset: float
    audio_track_override: int | None
    lines: tuple[LineRequest, ...]


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


def _line_request(raw: object, where: str) -> LineRequest:
    obj = json_object(raw, where)
    check_keys(obj, frozenset({"line_start", "line_expansion"}), frozenset({"line_start"}), where)
    start = opt_float(obj["line_start"], f"{where}.line_start")
    if start is None or start < 0:
        raise bad(f"{where}.line_start must be a number of 0 or more.")
    return LineRequest(start, parse_expansion(obj.get("line_expansion"), where))


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
        check_keys(ep, _MEDIA_EPISODE_KEYS, frozenset({"run_id", "video_file", "subtitle_file", "lines"}), where)
        raw_lines = ep["lines"]
        if not isinstance(raw_lines, list) or not raw_lines:
            raise bad(f"{where}.lines must list at least one line.")
        episodes.append(
            MediaEpisode(
                run_id=valid_run_id(ep["run_id"], f"{where}.run_id"),
                video_file=abs_path(text(ep["video_file"], f"{where}.video_file")),
                subtitle_file=abs_path(text(ep["subtitle_file"], f"{where}.subtitle_file")),
                subtitle_offset=opt_float(ep.get("subtitle_offset"), f"{where}.subtitle_offset") or 0.0,
                audio_track_override=opt_int(ep.get("audio_track_override"), f"{where}.audio_track_override"),
                lines=tuple(_line_request(line, f"{where}.lines[{j}]") for j, line in enumerate(raw_lines)),
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

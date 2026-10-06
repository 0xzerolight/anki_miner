"""The run file, validated into typed objects (API.md, "mine")."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from anki_miner.cli.api.contract import BAD_RUN_FILE, ApiError
from anki_miner.utils.bounded_reader import read_json_bounded

MAX_FILE_BYTES = 8 * 1024 * 1024
_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
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


def _bad(message: str) -> ApiError:
    return ApiError(BAD_RUN_FILE, message)


def read_json_file(path: Path) -> object:
    data = read_json_bounded(path, MAX_FILE_BYTES, _UNREADABLE, "API input")
    if data is _UNREADABLE:
        raise _bad(f"{path} cannot be read, or is not JSON.")
    return data


def _object(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise _bad(f"{where} must be a JSON object.")
    return value


def _keys(obj: Mapping[str, object], allowed: frozenset[str], required: frozenset[str], where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise _bad(f"{where} has unknown keys: {', '.join(unknown)}")
    missing = sorted(required - set(obj))
    if missing:
        raise _bad(f"{where} is missing: {', '.join(missing)}")


def _str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise _bad(f"{where} must be a string.")
    # json.loads accepts a lone "\ud83d" escape (a string cut inside an emoji),
    # but every file this run writes is UTF-8: refuse it now, before anything is
    # mined, not after the notes are added and the result cannot be written.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _bad(f"{where} is not valid text (it holds an unpaired surrogate).") from None
    return value


def _opt_str(value: object, where: str) -> str | None:
    return None if value is None else _str(value, where)


def _opt_int(value: object, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad(f"{where} must be a whole number.")
    return value


def _opt_float(value: object, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad(f"{where} must be a number.")
    try:
        number = float(value)
    except OverflowError:  # an int past float range
        number = math.inf
    if not math.isfinite(number):
        raise _bad(f"{where} must be a finite number.")
    return number


def _schema(obj: Mapping[str, object], where: str) -> None:
    schema = obj.get("schema")
    if isinstance(schema, bool) or schema != 1:
        raise _bad(f"{where}: schema must be 1.")


def _run_dir(value: object) -> Path:
    # Resolved, so a relative run_dir is taken from the caller's folder.
    path = Path(_str(value, "run_dir")).expanduser().resolve()
    if not path.is_dir():
        raise _bad(f"run_dir must be an existing folder: {path}")
    return path


def _run_id(value: object, where: str) -> str:
    run_id = _str(value, where)
    if not _RUN_ID.fullmatch(run_id):
        raise _bad(f"{where} may hold only letters, digits, '-' and '_' (at most 64).")
    return run_id


def _abs(value: str) -> Path:
    """An input path made absolute against the caller's working folder."""
    return Path(value).expanduser().resolve()


def _path(value: str | None) -> Path | None:
    return _abs(value) if value is not None else None


@dataclass(frozen=True)
class WordRequest:
    word: str
    line_start: float | None = None
    line_text: str | None = None
    line_expansion: tuple[int, int] | None = None
    surface: str | None = None
    reading: str | None = None


def _word_request(raw: object, where: str) -> WordRequest:
    obj = _object(raw, where)
    _keys(obj, _WORD_KEYS, frozenset({"word"}), where)
    word = _str(obj["word"], f"{where}.word")
    if not word.strip():
        raise _bad(f"{where}.word is empty.")
    line_start = _opt_float(obj.get("line_start"), f"{where}.line_start")
    if line_start is not None and line_start < 0:
        raise _bad(f"{where}.line_start cannot be negative.")
    line_text = _opt_str(obj.get("line_text"), f"{where}.line_text")
    if line_text is not None and not line_text.strip():
        raise _bad(f"{where}.line_text is empty.")
    raw_expansion = obj.get("line_expansion")
    expansion: tuple[int, int] | None = None
    if raw_expansion is not None:
        if not (
            isinstance(raw_expansion, list)
            and len(raw_expansion) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in raw_expansion)
        ):
            raise _bad(f"{where}.line_expansion must be [before, after], two whole numbers of 0 or more.")
        expansion = (raw_expansion[0], raw_expansion[1])
    surface = _opt_str(obj.get("surface"), f"{where}.surface")
    reading = _opt_str(obj.get("reading"), f"{where}.reading")
    for key, value in (("surface", surface), ("reading", reading)):
        if value is not None and not value.strip():
            raise _bad(f"{where}.{key} is empty.")
    return WordRequest(word, line_start, line_text, expansion, surface, reading)


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
    obj = _object(raw, where)
    _keys(obj, _EPISODE_KEYS, _EPISODE_REQUIRED, where)
    video_file = _abs(_str(obj["video_file"], f"{where}.video_file"))
    subtitle_file = _abs(_str(obj["subtitle_file"], f"{where}.subtitle_file"))
    secondary = _path(_opt_str(obj.get("secondary_subtitle_file"), f"{where}.secondary_subtitle_file"))
    raw_words = obj["words"]
    if not isinstance(raw_words, list) or not raw_words:
        raise _bad(f"{where}.words must list at least one word (there is no 'all').")
    words = tuple(_word_request(w, f"{where}.words[{j}]") for j, w in enumerate(raw_words))
    return Episode(
        run_id=_run_id(obj["run_id"], f"{where}.run_id"),
        video_file=video_file,
        subtitle_file=subtitle_file,
        subtitle_offset=_opt_float(obj.get("subtitle_offset"), f"{where}.subtitle_offset") or 0.0,
        audio_track_override=_opt_int(obj.get("audio_track_override"), f"{where}.audio_track_override"),
        source_label_override=_opt_str(obj.get("source_label_override"), f"{where}.source_label_override"),
        secondary_subtitle_file=secondary,
        secondary_subtitle_offset=_opt_float(obj.get("secondary_subtitle_offset"), f"{where}.secondary_subtitle_offset")
        or 0.0,
        series_name_override=_opt_str(obj.get("series_name_override"), f"{where}.series_name_override"),
        episode_name_override=_opt_str(obj.get("episode_name_override"), f"{where}.episode_name_override"),
        tags=_opt_str(obj.get("tags"), f"{where}.tags") or "",
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
    obj = _object(data, "The run file")
    _keys(
        obj,
        frozenset({"schema", "run_dir", "profile", "language", "config", "episodes", "dry_run"}),
        frozenset({"schema", "run_dir", "language", "episodes"}),
        "The run file",
    )
    _schema(obj, "The run file")
    dry_run = obj.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise _bad("dry_run must be true or false.")
    raw_episodes = obj["episodes"]
    if not isinstance(raw_episodes, list) or not raw_episodes:
        raise _bad("episodes must be a non-empty list.")
    episodes = tuple(parse_episode(raw, f"episodes[{i}]") for i, raw in enumerate(raw_episodes))
    ids = [episode.run_id for episode in episodes]
    if len(set(ids)) != len(ids):
        raise _bad("Two episodes share a run_id.")
    return RunFile(
        run_dir=_run_dir(obj["run_dir"]),
        profile=_opt_str(obj.get("profile"), "profile"),
        language=obj["language"],
        overlay=_object(obj.get("config", {}), "config"),
        episodes=episodes,
        dry_run=dry_run,
    )

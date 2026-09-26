"""The run file (prepare) and commit file, validated into typed objects (proposal, "Run file", "commit")."""

from __future__ import annotations

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
    }
)
_EPISODE_REQUIRED = frozenset({"run_id", "video_file", "subtitle_file"})


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
    return float(value)


def _schema(obj: Mapping[str, object], where: str) -> None:
    schema = obj.get("schema")
    if isinstance(schema, bool) or schema != 1:
        raise _bad(f"{where}: schema must be 1.")


def _run_dir(value: object) -> Path:
    # Resolved, so run.json and commit.json may spell the same folder differently.
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
class Episode:
    run_id: str
    video_file: Path
    subtitle_file: Path
    subtitle_offset: float | None
    audio_track_override: int | None
    source_label_override: str | None
    secondary_subtitle_file: Path | None
    secondary_subtitle_offset: float
    series_name_override: str | None
    episode_name_override: str | None
    tags: str
    #: The episode as the run file gave it; the saved run keeps it for commit.
    raw: Mapping[str, object]

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
    # The saved episode keeps the resolved paths: commit may run from another folder.
    resolved = {"video_file": str(video_file), "subtitle_file": str(subtitle_file)}
    if secondary is not None:
        resolved["secondary_subtitle_file"] = str(secondary)
    return Episode(
        run_id=_run_id(obj["run_id"], f"{where}.run_id"),
        video_file=video_file,
        subtitle_file=subtitle_file,
        subtitle_offset=_opt_float(obj.get("subtitle_offset"), f"{where}.subtitle_offset"),
        audio_track_override=_opt_int(obj.get("audio_track_override"), f"{where}.audio_track_override"),
        source_label_override=_opt_str(obj.get("source_label_override"), f"{where}.source_label_override"),
        secondary_subtitle_file=secondary,
        secondary_subtitle_offset=_opt_float(obj.get("secondary_subtitle_offset"), f"{where}.secondary_subtitle_offset")
        or 0.0,
        series_name_override=_opt_str(obj.get("series_name_override"), f"{where}.series_name_override"),
        episode_name_override=_opt_str(obj.get("episode_name_override"), f"{where}.episode_name_override"),
        tags=_opt_str(obj.get("tags"), f"{where}.tags") or "",
        raw={**obj, **resolved},
    )


@dataclass(frozen=True)
class RunFile:
    run_dir: Path
    profile: str | None
    language: object  # validated by settings.with_language
    overlay: Mapping[str, object]
    episodes: tuple[Episode, ...]


def parse_run_file(data: object) -> RunFile:
    obj = _object(data, "The run file")
    _keys(
        obj,
        frozenset({"schema", "run_dir", "profile", "language", "config", "episodes"}),
        frozenset({"schema", "run_dir", "language", "episodes"}),
        "The run file",
    )
    _schema(obj, "The run file")
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
    )


@dataclass(frozen=True)
class WordPick:
    mined_form: str
    line: int | None = None
    line_expansion: tuple[int, int] | None = None


@dataclass(frozen=True)
class CommitRun:
    run_id: str
    words: tuple[WordPick, ...]


@dataclass(frozen=True)
class CommitFile:
    run_dir: Path
    runs: tuple[CommitRun, ...]


def _pick(raw: object, where: str) -> WordPick:
    obj = _object(raw, where)
    _keys(obj, frozenset({"mined_form", "line", "line_expansion"}), frozenset({"mined_form"}), where)
    mined_form = _str(obj["mined_form"], f"{where}.mined_form")
    if not mined_form:
        raise _bad(f"{where}.mined_form is empty.")
    line = _opt_int(obj.get("line"), f"{where}.line")
    raw_expansion = obj.get("line_expansion")
    expansion: tuple[int, int] | None = None
    if raw_expansion is not None:
        if not (
            isinstance(raw_expansion, list)
            and len(raw_expansion) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) for n in raw_expansion)
        ):
            raise _bad(f"{where}.line_expansion must be [before, after].")
        expansion = (raw_expansion[0], raw_expansion[1])
    return WordPick(mined_form, line, expansion)


def parse_commit_file(data: object) -> CommitFile:
    obj = _object(data, "The commit file")
    required = frozenset({"schema", "run_dir", "runs"})
    _keys(obj, required, required, "The commit file")
    _schema(obj, "The commit file")
    raw_runs = obj["runs"]
    if not isinstance(raw_runs, list) or not raw_runs:
        raise _bad("runs must be a non-empty list.")
    runs: list[CommitRun] = []
    for i, raw in enumerate(raw_runs):
        run = _object(raw, f"runs[{i}]")
        _keys(run, frozenset({"run_id", "words"}), frozenset({"run_id", "words"}), f"runs[{i}]")
        words = run["words"]
        if not isinstance(words, list) or not words:
            raise _bad(f"runs[{i}].words must list at least one word (there is no 'all').")
        picks = tuple(_pick(w, f"runs[{i}].words[{j}]") for j, w in enumerate(words))
        if len({p.mined_form for p in picks}) != len(picks):
            raise _bad(f"runs[{i}] names a word twice.")
        runs.append(CommitRun(_run_id(run["run_id"], f"runs[{i}].run_id"), picks))
    if len({r.run_id for r in runs}) != len(runs):
        raise _bad("Two runs share a run_id.")
    return CommitFile(_run_dir(obj["run_dir"]), tuple(runs))

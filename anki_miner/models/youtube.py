"""Data models for YouTube video mining."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SubMode = Literal["manual_only", "auto_only"]


@dataclass(frozen=True)
class VideoInfo:
    """Metadata about a YouTube video, gathered before download.

    Immutable to keep thread-safety guarantees consistent with the rest of
    the pipeline (see AnkiMinerConfig).
    """

    video_id: str
    title: str
    duration_s: int
    has_manual_ja_subs: bool
    has_auto_ja_subs: bool
    thumbnail_url: str | None
    uploader: str | None
    is_live: bool
    is_age_restricted: bool


@dataclass(frozen=True)
class FetchedMedia:
    """Paths to the downloaded video and subtitle files for a YouTube job.

    Accepts str or Path for file fields; str inputs are coerced to Path in
    ``__post_init__`` using ``object.__setattr__`` (same pattern as
    AnkiMinerConfig) to keep the dataclass frozen while still normalizing.
    """

    video_file: Path
    subtitle_file: Path
    sub_source: Literal["manual", "auto"]

    def __post_init__(self) -> None:
        """Convert string paths to Path objects if needed."""
        if isinstance(self.video_file, str):
            object.__setattr__(self, "video_file", Path(self.video_file))
        if isinstance(self.subtitle_file, str):
            object.__setattr__(self, "subtitle_file", Path(self.subtitle_file))


@dataclass(frozen=True)
class PlaylistEntry:
    """A single video entry within a YouTube playlist.

    Immutable to keep thread-safety guarantees consistent with the rest of
    the pipeline (see AnkiMinerConfig).  ``duration_s`` is optional because
    flat yt-dlp playlist extraction sometimes omits it.
    """

    video_id: str
    title: str
    duration_s: int | None  # flat extraction may omit duration
    url: str  # canonical https://www.youtube.com/watch?v=<id>


@dataclass(frozen=True)
class PlaylistInfo:
    """Metadata about a YouTube playlist, gathered before individual video downloads.

    Immutable to keep thread-safety guarantees consistent with the rest of
    the pipeline (see AnkiMinerConfig).  Both ``playlist_id`` and
    ``total_count`` are optional because yt-dlp may not provide them for all
    playlist types.
    """

    playlist_id: str | None
    title: str
    entries: tuple[PlaylistEntry, ...]
    total_count: int | None  # yt-dlp "playlist_count", may be absent

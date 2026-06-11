"""Tests for YouTube data models."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from anki_miner.models.youtube import FetchedMedia, PlaylistEntry, PlaylistInfo, VideoInfo


class TestVideoInfo:
    """Tests for the VideoInfo frozen dataclass."""

    def _make(self, **overrides) -> VideoInfo:
        defaults = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Sample Title",
            "duration_s": 213,
            "has_manual_ja_subs": True,
            "has_auto_ja_subs": False,
            "is_live": False,
            "is_age_restricted": False,
        }
        defaults.update(overrides)
        return VideoInfo(**defaults)

    def test_constructs_with_all_fields(self):
        """VideoInfo constructs and exposes every declared field."""
        info = self._make()
        assert info.video_id == "dQw4w9WgXcQ"
        assert info.title == "Sample Title"
        assert info.duration_s == 213
        assert info.has_manual_ja_subs is True
        assert info.has_auto_ja_subs is False
        assert info.is_live is False
        assert info.is_age_restricted is False

    def test_is_frozen(self):
        """VideoInfo is immutable; mutating any field raises FrozenInstanceError."""
        info = self._make()
        with pytest.raises(FrozenInstanceError):
            info.title = "New Title"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            info.is_live = True  # type: ignore[misc]

    def test_live_and_age_restricted_flags(self):
        """Flag fields round-trip as provided."""
        info = self._make(is_live=True, is_age_restricted=True)
        assert info.is_live is True
        assert info.is_age_restricted is True


class TestFetchedMedia:
    """Tests for the FetchedMedia frozen dataclass."""

    def test_accepts_path_directly(self, tmp_path: Path):
        """Path inputs pass through without modification."""
        video = tmp_path / "video.mp4"
        subs = tmp_path / "video.ja.srt"
        media = FetchedMedia(video_file=video, subtitle_file=subs, sub_source="manual")
        assert media.video_file is video
        assert media.subtitle_file is subs
        assert media.sub_source == "manual"

    def test_coerces_str_to_path(self, tmp_path: Path):
        """str paths are coerced to Path in __post_init__."""
        video_str = str(tmp_path / "video.mp4")
        subs_str = str(tmp_path / "video.ja.srt")
        media = FetchedMedia(
            video_file=video_str,  # type: ignore[arg-type]
            subtitle_file=subs_str,  # type: ignore[arg-type]
            sub_source="auto",
        )
        assert isinstance(media.video_file, Path)
        assert isinstance(media.subtitle_file, Path)
        assert media.video_file == Path(video_str)
        assert media.subtitle_file == Path(subs_str)
        assert media.sub_source == "auto"

    def test_coerces_mixed_str_and_path(self, tmp_path: Path):
        """Mixed str + Path inputs both land as Path."""
        video_path = tmp_path / "video.mp4"
        subs_str = str(tmp_path / "video.ja.srt")
        media = FetchedMedia(
            video_file=video_path,
            subtitle_file=subs_str,  # type: ignore[arg-type]
            sub_source="manual",
        )
        assert isinstance(media.video_file, Path)
        assert isinstance(media.subtitle_file, Path)

    def test_is_frozen(self, tmp_path: Path):
        """FetchedMedia is immutable; mutating any field raises FrozenInstanceError."""
        media = FetchedMedia(
            video_file=tmp_path / "video.mp4",
            subtitle_file=tmp_path / "video.ja.srt",
            sub_source="manual",
        )
        with pytest.raises(FrozenInstanceError):
            media.sub_source = "auto"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            media.video_file = tmp_path / "other.mp4"  # type: ignore[misc]

    # Note: ``sub_source`` is a typing.Literal. Python does not enforce Literal
    # membership at runtime, so there is no runtime rejection test here; mypy
    # handles this at type-check time.


class TestPlaylistEntry:
    """Tests for the PlaylistEntry frozen dataclass."""

    def _make(self, **overrides) -> "PlaylistEntry":
        defaults = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Never Gonna Give You Up",
            "duration_s": 213,
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }
        defaults.update(overrides)
        return PlaylistEntry(**defaults)

    def test_constructs_with_all_fields(self):
        """PlaylistEntry constructs and exposes every declared field."""
        entry = self._make()
        assert entry.video_id == "dQw4w9WgXcQ"
        assert entry.title == "Never Gonna Give You Up"
        assert entry.duration_s == 213
        assert entry.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_duration_s_accepts_none(self):
        """duration_s may be None when flat extraction omits it."""
        entry = self._make(duration_s=None)
        assert entry.duration_s is None

    def test_is_frozen(self):
        """PlaylistEntry is immutable; mutating any field raises FrozenInstanceError."""
        entry = self._make()
        with pytest.raises(FrozenInstanceError):
            entry.title = "Changed"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            entry.duration_s = 0  # type: ignore[misc]


class TestPlaylistInfo:
    """Tests for the PlaylistInfo frozen dataclass."""

    def _make_entry(self, video_id: str = "aaa", title: str = "Title") -> "PlaylistEntry":
        return PlaylistEntry(
            video_id=video_id,
            title=title,
            duration_s=60,
            url=f"https://www.youtube.com/watch?v={video_id}",
        )

    def _make(self, **overrides) -> "PlaylistInfo":
        defaults: dict = {
            "playlist_id": "PLtest123",
            "title": "My Playlist",
            "entries": (self._make_entry("aaa", "First"), self._make_entry("bbb", "Second")),
            "total_count": 2,
        }
        defaults.update(overrides)
        return PlaylistInfo(**defaults)

    def test_constructs_with_all_fields(self):
        """PlaylistInfo constructs and exposes every declared field."""
        info = self._make()
        assert info.playlist_id == "PLtest123"
        assert info.title == "My Playlist"
        assert len(info.entries) == 2
        assert info.total_count == 2

    def test_entries_is_tuple(self):
        """entries field is a tuple, not a list."""
        info = self._make()
        assert isinstance(info.entries, tuple)

    def test_entries_contains_playlist_entry_instances(self):
        """Each element in entries is a PlaylistEntry."""
        info = self._make()
        for entry in info.entries:
            assert isinstance(entry, PlaylistEntry)

    def test_playlist_id_accepts_none(self):
        """playlist_id may be None (e.g. bare search-result playlists)."""
        info = self._make(playlist_id=None)
        assert info.playlist_id is None

    def test_total_count_accepts_none(self):
        """total_count may be None when yt-dlp omits playlist_count."""
        info = self._make(total_count=None)
        assert info.total_count is None

    def test_empty_entries_tuple(self):
        """PlaylistInfo with an empty entries tuple is valid."""
        info = self._make(entries=(), total_count=0)
        assert info.entries == ()

    def test_is_frozen(self):
        """PlaylistInfo is immutable; mutating any field raises FrozenInstanceError."""
        info = self._make()
        with pytest.raises(FrozenInstanceError):
            info.title = "Changed"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            info.total_count = 99  # type: ignore[misc]

"""Tests for YouTube data models."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from anki_miner.models.youtube import FetchedMedia, VideoInfo


class TestVideoInfo:
    """Tests for the VideoInfo frozen dataclass."""

    def _make(self, **overrides) -> VideoInfo:
        defaults = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Sample Title",
            "duration_s": 213,
            "has_manual_ja_subs": True,
            "has_auto_ja_subs": False,
            "thumbnail_url": "https://img.youtube.com/vi/dQw4w9WgXcQ/hq.jpg",
            "uploader": "Some Channel",
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
        assert info.thumbnail_url == "https://img.youtube.com/vi/dQw4w9WgXcQ/hq.jpg"
        assert info.uploader == "Some Channel"
        assert info.is_live is False
        assert info.is_age_restricted is False

    def test_accepts_none_for_optional_fields(self):
        """thumbnail_url and uploader are allowed to be None."""
        info = self._make(thumbnail_url=None, uploader=None)
        assert info.thumbnail_url is None
        assert info.uploader is None

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

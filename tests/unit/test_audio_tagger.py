"""Tests for services/audio_tagger.py (Issue #113)."""

import base64
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from anki_miner.services import audio_tagger
from anki_miner.services.audio_tagger import (
    TaggingError,
    TrackMetadata,
    prefill_track_metadata,
    tag_audio_file,
)

META = TrackMetadata(title="Ep Title", album="Season 01", artist="Show", track=3, genre="Condensed Audio")


def _png(tmp_path: Path, size: tuple[int, int] = (1600, 900)) -> Path:
    path = tmp_path / "cover.png"
    Image.new("RGBA", size, (10, 20, 30, 255)).save(path)
    return path


class _FakeID3Tags:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.added: list[object] = []

    def delall(self, key: str) -> None:
        self.deleted.append(key)

    def add(self, frame: object) -> None:
        self.added.append(frame)


class TestMp3:
    def _run(self, monkeypatch, meta, tags):
        audio = MagicMock()
        audio.tags = tags
        if tags is None:

            def _add_tags() -> None:
                audio.tags = _FakeID3Tags()

            audio.add_tags.side_effect = _add_tags
        monkeypatch.setattr(audio_tagger, "MP3", MagicMock(return_value=audio))
        tag_audio_file(Path("x.mp3"), meta)
        return audio

    def test_frames_written(self, monkeypatch):
        audio = self._run(monkeypatch, META, tags=_FakeID3Tags())
        by_type = {type(f).__name__: f for f in audio.tags.added}
        assert by_type["TIT2"].text == ["Ep Title"]
        assert by_type["TALB"].text == ["Season 01"]
        assert by_type["TPE1"].text == ["Show"]
        assert by_type["TRCK"].text == ["3"]
        assert by_type["TCON"].text == ["Condensed Audio"]
        # ffmpeg-propagated source frames must be cleared, not duplicated.
        assert set(audio.tags.deleted) == {"TIT2", "TALB", "TPE1", "TRCK", "TCON", "APIC"}
        audio.save.assert_called_once_with(v2_version=3)

    def test_missing_header_added(self, monkeypatch):
        audio = self._run(monkeypatch, META, tags=None)
        audio.add_tags.assert_called_once()

    def test_empty_fields_not_written(self, monkeypatch):
        audio = self._run(monkeypatch, TrackMetadata(title="Only Title"), tags=_FakeID3Tags())
        assert [type(f).__name__ for f in audio.tags.added] == ["TIT2"]

    def test_artwork_apic(self, monkeypatch, tmp_path):
        meta = TrackMetadata(title="T", artwork_path=_png(tmp_path))
        audio = self._run(monkeypatch, meta, tags=_FakeID3Tags())
        apic = [f for f in audio.tags.added if type(f).__name__ == "APIC"]
        assert len(apic) == 1
        assert apic[0].mime == "image/jpeg"
        assert apic[0].type == 3


class TestOpusFlac:
    def test_opus_comments_and_picture(self, monkeypatch, tmp_path):
        audio = MagicMock()
        store: dict[str, list[str]] = {}
        audio.__setitem__.side_effect = store.__setitem__
        monkeypatch.setattr(audio_tagger, "OggOpus", MagicMock(return_value=audio))
        tag_audio_file(Path("x.opus"), TrackMetadata(title="T", track=3, artwork_path=_png(tmp_path)))
        assert store["title"] == ["T"]
        assert store["tracknumber"] == ["3"]
        decoded = base64.b64decode(store["metadata_block_picture"][0])
        assert decoded  # a serialized FLAC Picture block
        audio.save.assert_called_once()

    def test_opus_empty_fields_skipped(self, monkeypatch):
        audio = MagicMock()
        store: dict[str, list[str]] = {}
        audio.__setitem__.side_effect = store.__setitem__
        monkeypatch.setattr(audio_tagger, "OggOpus", MagicMock(return_value=audio))
        tag_audio_file(Path("x.opus"), TrackMetadata(title="T"))
        assert set(store) == {"title"}

    def test_flac_picture(self, monkeypatch, tmp_path):
        audio = MagicMock()
        monkeypatch.setattr(audio_tagger, "FLAC", MagicMock(return_value=audio))
        tag_audio_file(Path("x.flac"), TrackMetadata(title="T", artwork_path=_png(tmp_path)))
        audio.clear_pictures.assert_called_once()
        audio.add_picture.assert_called_once()
        pic = audio.add_picture.call_args.args[0]
        assert pic.mime == "image/jpeg"
        audio.save.assert_called_once()


class TestErrors:
    def test_unknown_suffix(self):
        with pytest.raises(TaggingError):
            tag_audio_file(Path("x.wav"), META)

    def test_mutagen_error_wrapped(self, monkeypatch):
        monkeypatch.setattr(audio_tagger, "MP3", MagicMock(side_effect=OSError("boom")))
        with pytest.raises(TaggingError, match="boom"):
            tag_audio_file(Path("x.mp3"), META)

    def test_bad_artwork_wrapped(self, monkeypatch, tmp_path):
        bad = tmp_path / "cover.png"
        bad.write_bytes(b"not an image")
        audio = MagicMock()
        audio.tags = _FakeID3Tags()
        monkeypatch.setattr(audio_tagger, "MP3", MagicMock(return_value=audio))
        with pytest.raises(TaggingError):
            tag_audio_file(Path("x.mp3"), TrackMetadata(artwork_path=bad))


class TestArtworkPrep:
    def test_reencode_caps_long_edge_and_strips_alpha(self, tmp_path):
        art = audio_tagger._prepare_artwork(_png(tmp_path, size=(1600, 900)))
        assert art.mime == "image/jpeg"
        assert max(art.width, art.height) == audio_tagger._ARTWORK_MAX_EDGE
        img = Image.open(BytesIO(art.data))
        assert img.format == "JPEG"
        assert img.mode == "RGB"

    def test_small_image_not_upscaled(self, tmp_path):
        art = audio_tagger._prepare_artwork(_png(tmp_path, size=(300, 200)))
        assert (art.width, art.height) == (300, 200)


class TestPrefill:
    def test_maps_parsed_fields(self):
        [meta] = prefill_track_metadata([Path("Show (2026) - S01E02 - Pilot.mkv")])
        assert meta == TrackMetadata(title="Pilot", album="Season 01", artist="Show (2026)", track=2)

    def test_fallbacks(self):
        [meta] = prefill_track_metadata([Path("Movie (2020).mkv")])
        assert meta == TrackMetadata(title="Movie (2020)", album="", artist="", track=None)

    def test_one_entry_per_input_in_order(self):
        metas = prefill_track_metadata([Path("Show - 01.mkv"), Path("Show - 02.mkv")])
        assert [m.track for m in metas] == [1, 2]

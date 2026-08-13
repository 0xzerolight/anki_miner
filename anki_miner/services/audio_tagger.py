"""Write ID3/VorbisComment metadata + embedded artwork into condensed audio (Issue #113).

Pure post-processing: the file is already fully encoded and atomically moved
into place; tagging rewrites it in place via mutagen. Callers treat failures
as best-effort (``TaggingError`` -> warning), mirroring the sidecar-write
contract in ``audio_condenser``.
"""

from __future__ import annotations

import base64
import functools
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, TALB, TCON, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus
from PIL import Image

from anki_miner.utils.episode_matcher import parse_media_filename
from anki_miner.utils.pil_limits import apply_pil_image_limits, validate_image_pixel_budget

apply_pil_image_limits()

_ARTWORK_MAX_EDGE = 1000
_ARTWORK_JPEG_QUALITY = 85
# Vorbis picture type 3 = front cover (same numbering as ID3 APIC).
_FRONT_COVER = 3


class TaggingError(RuntimeError):
    """Any tagging failure — callers surface it as a warning, never a failed run."""


@dataclass(frozen=True)
class TrackMetadata:
    """One output file's tags; empty string / None fields are skipped."""

    title: str = ""
    album: str = ""
    artist: str = ""
    track: int | None = None
    genre: str = ""
    artwork_path: Path | None = None


@dataclass(frozen=True)
class _Artwork:
    data: bytes
    mime: str
    width: int
    height: int


def tag_audio_file(path: Path, meta: TrackMetadata) -> None:
    """Tag *path* in place, dispatching on suffix. Raises only TaggingError."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            _tag_mp3(path, meta)
        elif suffix == ".opus":
            _tag_opus(path, meta)
        elif suffix == ".flac":
            _tag_flac(path, meta)
        else:
            raise TaggingError(f"unsupported audio format: {suffix}")
    except TaggingError:
        raise
    except Exception as exc:
        raise TaggingError(str(exc)) from exc


def prefill_track_metadata(media_paths: Sequence[Path]) -> list[TrackMetadata]:
    """Heuristic pre-fill for the metadata dialog, one entry per input file."""
    result: list[TrackMetadata] = []
    for path in media_paths:
        parsed = parse_media_filename(path)
        result.append(
            TrackMetadata(
                title=parsed.episode_title or path.stem,
                album=f"Season {parsed.season:02d}" if parsed.season is not None else "",
                artist=parsed.series or "",
                track=parsed.episode,
            )
        )
    return result


def _tag_mp3(path: Path, meta: TrackMetadata) -> None:
    audio = MP3(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    assert tags is not None
    # ffmpeg propagates source-container tags into the output; clear our six
    # frames before adding so they are replaced, not duplicated.
    for frame in ("TIT2", "TALB", "TPE1", "TRCK", "TCON", "APIC"):
        tags.delall(frame)
    if meta.title:
        tags.add(TIT2(encoding=3, text=[meta.title]))
    if meta.album:
        tags.add(TALB(encoding=3, text=[meta.album]))
    if meta.artist:
        tags.add(TPE1(encoding=3, text=[meta.artist]))
    if meta.track is not None:
        tags.add(TRCK(encoding=3, text=[str(meta.track)]))
    if meta.genre:
        tags.add(TCON(encoding=3, text=[meta.genre]))
    if meta.artwork_path is not None:
        art = _prepare_artwork(meta.artwork_path)
        tags.add(APIC(encoding=3, mime=art.mime, type=_FRONT_COVER, desc="Cover", data=art.data))
    # v2.3 for broad player compatibility (mutagen defaults to v2.4).
    audio.save(v2_version=3)


def _tag_opus(path: Path, meta: TrackMetadata) -> None:
    audio = OggOpus(path)
    _set_vorbis_comments(audio, meta)
    if meta.artwork_path is not None:
        picture = _make_picture(_prepare_artwork(meta.artwork_path))
        # Ogg Opus has no picture stream; the spec embeds a base64 FLAC
        # Picture block in this vorbiscomment key.
        audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
    audio.save()


def _tag_flac(path: Path, meta: TrackMetadata) -> None:
    audio = FLAC(path)
    _set_vorbis_comments(audio, meta)
    if meta.artwork_path is not None:
        audio.clear_pictures()
        audio.add_picture(_make_picture(_prepare_artwork(meta.artwork_path)))
    audio.save()


def _set_vorbis_comments(audio: FLAC | OggOpus, meta: TrackMetadata) -> None:
    values = {
        "title": meta.title,
        "album": meta.album,
        "artist": meta.artist,
        "tracknumber": str(meta.track) if meta.track is not None else "",
        "genre": meta.genre,
    }
    for key, value in values.items():
        if value:
            audio[key] = [value]


def _make_picture(art: _Artwork) -> Picture:
    picture = Picture()
    picture.type = _FRONT_COVER
    picture.mime = art.mime
    picture.desc = "Cover"
    picture.width = art.width
    picture.height = art.height
    picture.depth = 24
    picture.data = art.data
    return picture


def _prepare_artwork(path: Path) -> _Artwork:
    return _prepare_artwork_cached(str(path), path.stat().st_mtime_ns)


@functools.lru_cache(maxsize=4)
def _prepare_artwork_cached(path_str: str, _mtime_ns: int) -> _Artwork:
    """Re-encode user artwork to bounded JPEG.

    Deterministic mime (no sniffing), alpha flattened, longest edge capped —
    an unbounded PNG would bloat every Opus output by +33% via base64.
    """
    with Image.open(path_str) as img:
        validate_image_pixel_budget(img)
        converted = img.convert("RGB")
    converted.thumbnail((_ARTWORK_MAX_EDGE, _ARTWORK_MAX_EDGE), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    converted.save(buffer, format="JPEG", quality=_ARTWORK_JPEG_QUALITY)
    return _Artwork(buffer.getvalue(), "image/jpeg", converted.width, converted.height)

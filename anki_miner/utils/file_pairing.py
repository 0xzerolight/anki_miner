"""Utility for pairing video and subtitle files across folders."""

import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SUBTITLE_PRIORITY: tuple[str, ...] = (".ass", ".ssa", ".srt")

# Case folding is correct only where the filesystem is case-insensitive (Windows,
# default macOS). On a case-sensitive volume, folding would treat two genuinely
# distinct files as the same and an overwrite would destroy the wrong one.
_CASE_INSENSITIVE_FS = sys.platform in ("win32", "darwin")


def _nfc(name: str) -> str:
    """NFC-normalize a filename string for robust comparison across sources."""
    return unicodedata.normalize("NFC", name)


def _name_match_key(name: str) -> str:
    """Comparison key for a full filename used to resolve an output write target.

    NFC always: NTFS stores exact UTF-16 and never normalizes, so an NFC request
    otherwise never matches an existing NFD file (the duplicate-subtitle bug).
    Casefold only on a case-insensitive FS, so case-distinct files on a
    case-sensitive volume are never collapsed into a destructive overwrite.
    macOS folds NFC/NFD itself, so this is effectively a Windows-NTFS fix.
    """
    key = _nfc(name)
    return key.casefold() if _CASE_INSENSITIVE_FS else key


def resolve_output_path(out_dir: Path, name: str) -> Path:
    """Return the exact path the caller should write/replace for *name* in *out_dir*.

    Returns an EXISTING file when one is the "same" file as *name* up to NFC
    normalization (and case, on a case-insensitive FS), so an overwrite replaces
    it in place instead of creating a visually-identical twin that Windows treats
    as a separate file. The returned path may already exist — the caller will
    overwrite it.

    Safety: a byte-exact match wins outright. If two or more DISTINCT files match
    only after normalization (and none is byte-exact), this refuses to guess and
    returns ``out_dir / name`` (write the exact requested bytes) so no unrelated
    subtitle is clobbered. Same fallback when *out_dir* is unreadable or holds no
    match.
    """
    exact = out_dir / name
    try:
        entries = sorted(p for p in out_dir.iterdir() if p.is_file())
    except OSError:
        return exact
    target = _name_match_key(name)
    matches: list[Path] = []
    for p in entries:
        if p.name == name:  # byte-exact wins outright
            return p
        if _name_match_key(p.name) == target:
            matches.append(p)
    if len(matches) == 1:
        return matches[0]
    return exact  # 0 matches -> create; >=2 ambiguous -> refuse to guess


def find_sibling_subtitle(video_path: Path) -> Path | None:
    """Return the highest-priority sibling subtitle for *video_path*, or None.

    Looks in the same folder for a file whose stem matches *video_path*'s stem
    and whose extension is one of DEFAULT_SUBTITLE_PRIORITY.  Returns the first
    hit in priority order (.ass > .ssa > .srt), or None when no sibling exists.

    Matching is case-insensitive on both stem and extension, and NFC-normalized
    on the stem, so a ``.SRT`` (a differing-case stem, or an NFD-encoded stem) is
    still found on case-sensitive filesystems. Reads are non-destructive, so the
    casefold here is unconditional (unlike the write-side resolver).
    """
    folder = video_path.parent
    stem_cf = _nfc(video_path.stem).casefold()
    try:
        entries = [p for p in folder.iterdir() if p.is_file()]
    except OSError:
        return None
    by_ext: dict[str, Path] = {}
    for p in entries:
        ext = p.suffix.lower()
        if ext in DEFAULT_SUBTITLE_PRIORITY and _nfc(p.stem).casefold() == stem_cf:
            by_ext.setdefault(ext, p)
    for ext in DEFAULT_SUBTITLE_PRIORITY:
        if ext in by_ext:
            return by_ext[ext]
    return None


@dataclass
class FilePair:
    """Represents a video/subtitle file pair."""

    video: Path
    subtitle: Path


class FilePairMatcher:
    """Matches video and subtitle files by base name, with deterministic
    format priority when multiple subtitle variants exist for one video.
    """

    VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mkv", ".avi", ".m4v", ".mov"})
    SUBTITLE_EXTENSIONS: frozenset[str] = frozenset(DEFAULT_SUBTITLE_PRIORITY)

    @staticmethod
    def find_pairs_by_episode_number(video_folder: Path, subtitle_folder: Path) -> list[FilePair]:
        """Find matching pairs by episode number instead of exact name.

        Matches files like:
        - Jujutsu_Kaisen_01.mp4 ↔ jjk_ep01.ass (both episode 1)
        - S01E05.mkv ↔ 05.srt (both episode 5)
        - video_1.mp4 ↔ episode_01.ass (both episode 1, different padding)

        Args:
            video_folder: Folder containing video files
            subtitle_folder: Folder containing subtitle files

        Returns:
            List of FilePair objects matched by episode number
        """
        from anki_miner.utils.episode_matcher import EpisodeMatcher

        # Get all videos and subtitles
        videos = [
            f for f in video_folder.iterdir() if f.is_file() and f.suffix.lower() in FilePairMatcher.VIDEO_EXTENSIONS
        ]

        subtitles = [
            f
            for f in subtitle_folder.iterdir()
            if f.is_file() and f.suffix.lower() in FilePairMatcher.SUBTITLE_EXTENSIONS
        ]

        # Match by episode number
        matched_pairs = EpisodeMatcher.match_by_episode_number(videos, subtitles)

        # Convert to FilePair objects
        return [FilePair(video, subtitle) for video, subtitle in matched_pairs]

"""Utility for pairing video and subtitle files across folders."""

import sys
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SUBTITLE_PRIORITY: tuple[str, ...] = (".ass", ".ssa", ".srt")

# Explicit case folding is needed only on Windows. On macOS, preserving the
# requested spelling lets the mounted volume decide whether case variants alias
# or name distinct files, avoiding destructive matches on case-sensitive volumes.
_CASE_INSENSITIVE_FS = sys.platform == "win32"


def _nfc(name: str) -> str:
    """NFC-normalize a filename string for robust comparison across sources."""
    return unicodedata.normalize("NFC", name)


def _name_match_key(name: str) -> str:
    """Comparison key for a full filename used to resolve an output write target.

    NFC always: NTFS stores exact UTF-16 and never normalizes, so an NFC request
    otherwise never matches an existing NFD file (the duplicate-subtitle bug).
    Casefold only on Windows. macOS keeps the requested case and lets the mounted
    volume decide whether it aliases an existing path, so case-distinct files on
    case-sensitive volumes are never collapsed into a destructive overwrite.
    """
    key = _nfc(name)
    return key.casefold() if _CASE_INSENSITIVE_FS else key


def output_path_identity(path: Path) -> tuple[Path, str | None]:
    """Return canonical identity for an existing or planned write target."""
    if path.exists():
        return path.resolve(), None
    return path.parent.resolve(), _name_match_key(path.name)


def resolve_output_path(out_dir: Path, name: str) -> Path:
    """Return the exact path the caller should write/replace for *name* in *out_dir*.

    Returns an EXISTING file when one is the "same" file as *name* up to NFC
    normalization (and case on Windows), so an overwrite replaces it in place
    instead of creating a visually-identical twin that Windows treats as a
    separate file. The returned path may already exist — the caller will overwrite
    it.

    Safety: a byte-exact match wins outright. If two or more DISTINCT files match
    only after normalization (and none is byte-exact), this refuses to guess and
    returns ``out_dir / name`` (write the exact requested bytes) so no unrelated
    subtitle is clobbered. Same fallback when *out_dir* is unreadable or holds no
    match.
    """
    return resolve_output_paths(out_dir, [name])[0]


def resolve_output_paths(out_dir: Path, names: Sequence[str]) -> list[Path]:
    """Resolve several output names from one snapshot of *out_dir*.

    Each name follows :func:`resolve_output_path`'s exact/NFC/platform-case
    contract. The directory is scanned and indexed once for the whole batch.
    New names are reserved under the same match key so equivalent names planned
    before either exists resolve to one target.
    """
    exact_paths = [out_dir / name for name in names]
    try:
        entries = sorted(p for p in out_dir.iterdir() if p.is_file())
    except OSError:
        entries = []

    exact_by_name = {path.name: path for path in entries}
    matches_by_key: dict[str, list[Path]] = {}
    for p in entries:
        matches_by_key.setdefault(_name_match_key(p.name), []).append(p)

    resolved: list[Path] = []
    planned_by_key: dict[str, Path] = {}
    for name, exact in zip(names, exact_paths, strict=True):
        byte_exact = exact_by_name.get(name)
        if byte_exact is not None:
            resolved.append(byte_exact)
            continue
        match_key = _name_match_key(name)
        matches = matches_by_key.get(match_key, [])
        if len(matches) == 1:
            resolved.append(matches[0])
        elif matches:
            resolved.append(exact)
        else:
            planned = planned_by_key.setdefault(match_key, exact)
            resolved.append(planned)
    return resolved


def find_sibling_subtitle(video_path: Path, priority: Sequence[str] | None = None) -> Path | None:
    """Return the highest-priority sibling subtitle for *video_path*, or None.

    Looks in the same folder for a file whose stem matches *video_path*'s stem
    and whose extension is one of *priority*.  Returns the best match in priority
    order, preferring an exact stem, or None when no unambiguous sibling exists.

    Args:
        video_path: Video (or media) file whose sibling subtitle is sought.
        priority: Ordered lowercase extensions (e.g. ``(".ass", ".srt")``) to
            accept, best first.  Defaults to :data:`DEFAULT_SUBTITLE_PRIORITY`
            (``.ass > .ssa > .srt``), preserving mining behavior byte-for-byte.
            Callers may pass a wider set (e.g. including ``.vtt``).

    Matching is case-insensitive on both stem and extension, and NFC-normalized
    on the stem, so a ``.SRT`` (a differing-case stem, or an NFD-encoded stem) is
    still found on case-sensitive filesystems. Reads are non-destructive, so the
    casefold here is unconditional (unlike the write-side resolver). Within one
    extension, an exact stem wins; multiple normalization-only matches are
    ambiguous and return ``None`` rather than depending on directory order.
    """
    exts = DEFAULT_SUBTITLE_PRIORITY if priority is None else tuple(priority)
    folder = video_path.parent
    stem_cf = _nfc(video_path.stem).casefold()
    try:
        entries = [p for p in folder.iterdir() if p.is_file()]
    except OSError:
        return None
    by_ext: dict[str, list[Path]] = {}
    for p in entries:
        ext = p.suffix.lower()
        if ext in exts and _nfc(p.stem).casefold() == stem_cf:
            by_ext.setdefault(ext, []).append(p)
    for ext in exts:
        candidates = by_ext.get(ext, [])
        exact = next((p for p in candidates if p.stem == video_path.stem), None)
        if exact is not None:
            return exact
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            return None
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
    def find_pairs_by_episode_number(
        video_folder: Path,
        subtitle_folder: Path,
        video_extensions: Collection[str] | None = None,
        subtitle_extensions: Collection[str] | None = None,
    ) -> list[FilePair]:
        """Find matching pairs by episode number instead of exact name.

        Matches files like:
        - Jujutsu_Kaisen_01.mp4 ↔ jjk_ep01.ass (both episode 1)
        - S01E05.mkv ↔ 05.srt (both episode 5)
        - video_1.mp4 ↔ episode_01.ass (both episode 1, different padding)

        Args:
            video_folder: Folder containing video files
            subtitle_folder: Folder containing subtitle files
            video_extensions: Lowercase media extensions to accept as the
                "video" side.  Defaults to :data:`VIDEO_EXTENSIONS`, preserving
                mining behavior byte-for-byte.  Callers may pass a wider set
                (e.g. audio-only extensions).
            subtitle_extensions: Lowercase subtitle extensions to accept.
                Defaults to :data:`SUBTITLE_EXTENSIONS`, preserving mining
                behavior.  Callers may pass a wider set (e.g. including ``.vtt``).

        Returns:
            List of FilePair objects matched by episode number
        """
        from anki_miner.utils.episode_matcher import EpisodeMatcher

        video_exts = FilePairMatcher.VIDEO_EXTENSIONS if video_extensions is None else video_extensions
        subtitle_exts = FilePairMatcher.SUBTITLE_EXTENSIONS if subtitle_extensions is None else subtitle_extensions

        # Get all videos and subtitles. A folder that vanished, was never
        # created, or is actually a file (all OSError subclasses on iterdir)
        # yields no pairs rather than escaping — an unhandled FileNotFoundError
        # here reaches a Qt slot and aborts the whole process. Matches the
        # module's except-OSError idiom (resolve_output_path, find_sibling_subtitle).
        try:
            videos = [f for f in video_folder.iterdir() if f.is_file() and f.suffix.lower() in video_exts]
            subtitles = [f for f in subtitle_folder.iterdir() if f.is_file() and f.suffix.lower() in subtitle_exts]
        except OSError:
            return []

        # Deterministic video order: iterdir() order is filesystem-dependent, and
        # when episode extraction collapses several videos onto one number the
        # match outcome would otherwise depend on directory enumeration order
        # while the subtitle side is fully sorted — a shuffle that silently pairs
        # episode N's subtitle with episode M's video.
        videos.sort(key=lambda video: (_nfc(video.name), video.name))

        subtitle_priority = {suffix: index for index, suffix in enumerate(DEFAULT_SUBTITLE_PRIORITY)}
        subtitles.sort(
            key=lambda subtitle: (
                subtitle_priority.get(subtitle.suffix.lower(), len(DEFAULT_SUBTITLE_PRIORITY)),
                subtitle.suffix.lower(),
                _nfc(subtitle.name),
                # NFC collapses canonically equivalent spellings to one key; the
                # raw name makes the order total so iterdir() order can't decide.
                subtitle.name,
            )
        )

        # Match by episode number
        matched_pairs = EpisodeMatcher.match_by_episode_number(videos, subtitles)

        # Convert to FilePair objects
        return [FilePair(video, subtitle) for video, subtitle in matched_pairs]

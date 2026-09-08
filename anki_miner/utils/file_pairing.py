"""Utility for pairing video and subtitle files across folders."""

import logging
import sys
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from anki_miner.utils.logging_ext import suppressed

logger = logging.getLogger(__name__)

#: Mining subtitle formats, best first. Richest format wins when a folder holds
#: several variants for one episode: ASS/SSA carry styling and typesetting, SRT
#: carries plain cues, and WebVTT is the poorest of the four (its positioning
#: and cue settings are dropped on parse), so it sorts last and is only ever
#: picked when nothing better sits beside the video.
DEFAULT_SUBTITLE_PRIORITY: tuple[str, ...] = (".ass", ".ssa", ".srt", ".vtt")

#: Stem suffix Utilities → Retime appends to its output, so a retimed subtitle
#: sits beside the original instead of replacing it. Discovery prefers a file
#: carrying it: with ``EP01.srt`` and ``EP01_retimed.srt`` in one folder, mining
#: the off-timed original would silently undo the retime.
RETIMED_SUFFIX = "_retimed"

# Explicit case folding is needed only on Windows. On macOS, preserving the
# requested spelling lets the mounted volume decide whether case variants alias
# or name distinct files, avoiding destructive matches on case-sensitive volumes.
_CASE_INSENSITIVE_FS = sys.platform == "win32"


def _nfc(name: str) -> str:
    """NFC-normalize a filename string for robust comparison across sources."""
    return unicodedata.normalize("NFC", name)


def _is_retimed(path: Path) -> bool:
    """Return whether *path* is a Retime output (``<stem>_retimed.<ext>``)."""
    return _nfc(path.stem).casefold().endswith(RETIMED_SUFFIX)


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
    # WARNING, not silent: an unreadable folder makes every name resolve to the
    # exact spelling, so an existing NFD/case-variant file is left in place and
    # the write lands beside it as a visually-identical twin.
    entries: list[Path] = []
    with suppressed(logger, f"scanning output folder {out_dir}", level=logging.WARNING):
        entries = sorted(p for p in out_dir.iterdir() if p.is_file())

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

    Looks in the same folder for a file whose stem matches *video_path*'s stem —
    or that stem plus :data:`RETIMED_SUFFIX` — and whose extension is one of
    *priority*.  Returns the best match in priority order, preferring a retimed
    sibling and then an exact stem, or None when no unambiguous sibling exists.

    Args:
        video_path: Video (or media) file whose sibling subtitle is sought.
        priority: Ordered lowercase extensions (e.g. ``(".ass", ".srt")``) to
            accept, best first.  Defaults to :data:`DEFAULT_SUBTITLE_PRIORITY`
            (``.ass > .ssa > .srt > .vtt``).  Callers may pass a narrower set to
            exclude a format, or a wider one to accept extras.

    Matching is case-insensitive on both stem and extension, and NFC-normalized
    on the stem, so a ``.SRT`` (a differing-case stem, or an NFD-encoded stem) is
    still found on case-sensitive filesystems. Reads are non-destructive, so the
    casefold here is unconditional (unlike the write-side resolver). Within one
    extension the retimed group is tried first and an exact stem wins inside a
    group; multiple normalization-only matches are ambiguous and return ``None``
    rather than depending on directory order. The retimed group is preferred
    ahead of the extension priority, so ``EP01_retimed.srt`` beats ``EP01.ass``.
    """
    exts = DEFAULT_SUBTITLE_PRIORITY if priority is None else tuple(priority)
    folder = video_path.parent
    stem_cf = _nfc(video_path.stem).casefold()
    retimed_cf = stem_cf + RETIMED_SUFFIX
    # WARNING, not silent: no sibling means the caller mines without a
    # subtitle, and an unreadable folder is indistinguishable from an empty one.
    entries: list[Path] = []
    with suppressed(logger, f"scanning {folder} for a sibling subtitle", level=logging.WARNING):
        entries = [p for p in folder.iterdir() if p.is_file()]
    if not entries:
        return None
    by_group: dict[tuple[bool, str], list[Path]] = {}
    for p in entries:
        ext = p.suffix.lower()
        if ext not in exts:
            continue
        p_stem_cf = _nfc(p.stem).casefold()
        if p_stem_cf == retimed_cf:
            by_group.setdefault((True, ext), []).append(p)
        elif p_stem_cf == stem_cf:
            by_group.setdefault((False, ext), []).append(p)
    for retimed in (True, False):
        wanted_stem = video_path.stem + RETIMED_SUFFIX if retimed else video_path.stem
        for ext in exts:
            candidates = by_group.get((retimed, ext), [])
            exact = next((p for p in candidates if p.stem == wanted_stem), None)
            if exact is not None:
                return exact
            if len(candidates) == 1:
                return candidates[0]
            if candidates:
                return None
    return None


def _sort_subtitles(subtitles: list[Path], prefer_retimed: bool) -> None:
    """Order subtitle candidates in place, best-match-first for episode pairing.

    Pure ordering, no I/O: the caller owns the scan and its one warning. Applied
    to the mining track and to the secondary-language track (F7) alike, so both
    get the same format priority and the same retimed preference.
    """
    subtitle_priority = {suffix: index for index, suffix in enumerate(DEFAULT_SUBTITLE_PRIORITY)}
    subtitles.sort(
        key=lambda subtitle: (
            # Ahead of the format priority: a retimed .srt is a better match
            # for its video than the original .ass it was made from.
            not (prefer_retimed and _is_retimed(subtitle)),
            subtitle_priority.get(subtitle.suffix.lower(), len(DEFAULT_SUBTITLE_PRIORITY)),
            subtitle.suffix.lower(),
            _nfc(subtitle.name),
            # NFC collapses canonically equivalent spellings to one key; the
            # raw name makes the order total so iterdir() order can't decide.
            subtitle.name,
        )
    )


def _attach_secondary(
    pairs: list["FilePair"],
    videos: list[Path],
    secondary_folder: Path,
    subtitle_folder: Path,
    subtitle_exts: Collection[str],
    prefer_retimed: bool,
) -> None:
    """Fill each pair's ``secondary`` from *secondary_folder*, by episode number.

    The mining track's match rule, run a second time over the same (already
    sorted) videos. A subtitle is consumed once
    (:meth:`EpisodeMatcher.match_by_episode_number`, Issue #39), so the two
    folders have to be distinct: pointing both at one folder would hand every
    card its own sentence as its translation, and that is refused here, once,
    rather than in each of the four callers.

    An episode with no match keeps ``secondary=None`` and mines with an empty
    Translation field — a partial translation set never fails the run.
    """
    from anki_miner.utils.episode_matcher import EpisodeMatcher

    if secondary_folder == subtitle_folder or secondary_folder.resolve() == subtitle_folder.resolve():
        logger.warning(
            "secondary subtitles: the translation folder is the subtitle folder (%s); no translations attached",
            secondary_folder,
        )
        return
    # Its own scan and its own warning: a separate folder the user chose
    # separately, so a failure to read it is a separate fact from the pairing
    # scan's — which keeps that scan at exactly one line, as it has always been.
    secondary_subs: list[Path] = []
    scanned = False
    with suppressed(logger, f"scanning {secondary_folder} for translation subtitles", level=logging.WARNING):
        secondary_subs = [f for f in secondary_folder.iterdir() if f.is_file() and f.suffix.lower() in subtitle_exts]
        scanned = True
    _sort_subtitles(secondary_subs, prefer_retimed)
    if not secondary_subs:
        # A failed scan already has its WARNING above — this line is for a
        # readable folder with nothing usable in it.
        if scanned:
            logger.info("secondary subtitles: no candidates in %s", secondary_folder)
        return

    by_video = dict(EpisodeMatcher.match_by_episode_number(videos, secondary_subs))
    for pair in pairs:
        pair.secondary = by_video.get(pair.video)
    missing = [pair.video.stem for pair in pairs if pair.secondary is None]
    logger.info(
        "secondary subtitles: %d/%d episodes matched from %s%s",
        len(pairs) - len(missing),
        len(pairs),
        secondary_folder,
        f"; no translation for {', '.join(missing[:5])}" if missing else "",
    )


@dataclass
class FilePair:
    """Represents a video/subtitle file pair, optionally with a translation track."""

    video: Path
    subtitle: Path
    #: Secondary-language subtitle for this episode (F7), matched by episode
    #: number out of a third folder. ``None`` when no translation folder was
    #: given, when this episode had no match inside it, or when it could not be
    #: read — all three mine the episode with an empty Translation field.
    secondary: Path | None = None


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
        prefer_retimed: bool = True,
        *,
        secondary_folder: Path | None = None,
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
                Defaults to :data:`SUBTITLE_EXTENSIONS`.  Callers may pass their
                own set (Condense supplies its own so its media-side extensions
                stay in step with its subtitle-side ones).
            prefer_retimed: When True (the default) a ``<stem>_retimed`` subtitle
                outranks every other candidate for the same episode, so mining a
                folder that also holds the off-timed original uses the retime.
                Utilities → Retime passes False: its own input must be the
                original, not the output of its previous run.
            secondary_folder: Optional folder of secondary-language subtitles
                (F7), matched to the same videos by the same episode-number
                rule and hung off each pair's ``secondary``.  It has to be a
                folder of its own — a subtitle is consumed once, so two tracks
                for one episode cannot both be matched out of one folder — and
                an episode with no match there keeps ``secondary=None``.

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
        # WARNING, not silent: zero pairs is what the user sees, and "the
        # folder is empty" and "the folder could not be read" look identical
        # from the batch screen.
        videos: list[Path] = []
        subtitles: list[Path] = []
        with suppressed(logger, f"scanning {video_folder} and {subtitle_folder} for pairs", level=logging.WARNING):
            videos = [f for f in video_folder.iterdir() if f.is_file() and f.suffix.lower() in video_exts]
            subtitles = [f for f in subtitle_folder.iterdir() if f.is_file() and f.suffix.lower() in subtitle_exts]
        if not videos or not subtitles:
            return []

        # Deterministic video order: iterdir() order is filesystem-dependent, and
        # when episode extraction collapses several videos onto one number the
        # match outcome would otherwise depend on directory enumeration order
        # while the subtitle side is fully sorted — a shuffle that silently pairs
        # episode N's subtitle with episode M's video.
        videos.sort(key=lambda video: (_nfc(video.name), video.name))
        _sort_subtitles(subtitles, prefer_retimed)

        # Match by episode number
        matched_pairs = EpisodeMatcher.match_by_episode_number(videos, subtitles)

        # Convert to FilePair objects
        pairs = [FilePair(video, subtitle) for video, subtitle in matched_pairs]
        if secondary_folder is not None:
            _attach_secondary(pairs, videos, secondary_folder, subtitle_folder, subtitle_exts, prefer_retimed)
        return pairs

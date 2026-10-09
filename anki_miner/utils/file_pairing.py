"""Utility for pairing video and subtitle files across folders."""

import logging
import re
import sys
import unicodedata
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from anki_miner.utils.audio_track_detector import matches_language_tag
from anki_miner.utils.episode_matcher import EpisodeInfo, EpisodeMatcher, EpisodeNumberExtractor
from anki_miner.utils.file_utils import is_junk_path
from anki_miner.utils.logging_ext import suppressed

logger = logging.getLogger(__name__)

#: Mining subtitle formats, best first. Richest format wins when a folder holds
#: several variants for one episode: ASS/SSA carry styling and typesetting, SRT
#: carries plain cues, and WebVTT drops its positioning and cue settings on
#: parse. SAMI (.smi), the long-standing Korean fansub format, is poorer still:
#: it states no cue ends (the parser ends each cue at the next SYNC) and its
#: styling is dropped, so it sorts last and is only picked when nothing better
#: sits beside the video. Utilities -> Retime takes every format but SAMI
#: (``gui.constants.RETIME_SUBTITLE_EXTENSIONS``).
DEFAULT_SUBTITLE_PRIORITY: tuple[str, ...] = (".ass", ".ssa", ".srt", ".vtt", ".smi")

#: Stem suffix Utilities → Retime appends to its output, so a retimed subtitle
#: sits beside the original instead of replacing it. Discovery prefers a file
#: carrying it: with ``EP01.srt`` and ``EP01_retimed.srt`` in one folder, mining
#: the off-timed original would silently undo the retime.
RETIMED_SUFFIX = "_retimed"

#: Folders beside a video that hold its subtitles in a release layout, compared
#: casefolded: ``Subs/EP01.ja.srt``, or one folder per video, ``Subs/EP01/3_Japanese.srt``.
_SUBTITLE_DIR_NAMES = frozenset({"subs", "sub", "subtitles", "subtitle"})

#: What may follow a video's stem in the name of a subtitle made for it:
#: ``EP01.ja.srt``, ``EP01_track3_[jpn].ass``, ``EP01 [jpn].srt``, ``EP01-ja.srt``.
_TAG_SEPARATORS = frozenset("._ -[(")

#: Media that owns its own subtitle, beyond ``FilePairMatcher.VIDEO_EXTENSIONS``:
#: Utilities -> Download writes .webm and audio beside videos, and a video with
#: one of those beside it is not alone in its folder.
_OTHER_MEDIA_EXTENSIONS = frozenset({".webm", ".mka", ".mp3", ".m4a", ".m4b", ".aac", ".flac", ".ogg", ".opus", ".wav"})

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


#: Splits a run of filename tags into tokens: ``.ja.forced`` -> ``ja``, ``forced``;
#: ``_track3_[jpn]`` -> ``track3``, ``jpn``.
_TAG_SPLIT = re.compile(r"[\s._\[\](){}]+")

#: The same split for the tags after a video's name, but not at spaces:
#: ``- Just Do It`` stays one token, so an episode title's words never read as codes.
_TAIL_SPLIT = re.compile(r"[._\[\](){}]+")

#: Flags a release puts after the language tag (``Movie.en.sdh.forced``,
#: ``EP01.en.default.forced``); skipped, so the tag before them is still read.
_FLAG_TOKENS = frozenset({"forced", "sdh", "cc", "hi", "default"})

#: A bracketed group closing the name, where mkvextract and fansub tools put the
#: language: ``EP01_track4_[eng]``, ``Show - 01 [jpn]``.
_TRAILING_GROUP = re.compile(r"[\[(]([^\[\]()]+)[\])]\s*$")


@dataclass(frozen=True)
class SubtitleLanguage:
    """The language tags a subtitle filename is read against.

    ``mining`` is the mining language's ``audio_track_codes`` and caption codes;
    ``known`` is every mining language's, so a token outside it (a title word,
    ``forced``, ``sdh``) is never taken for a language. Built by
    :func:`anki_miner.languages.registry.subtitle_language`: ``utils`` cannot
    reach the profiles itself.
    """

    mining: frozenset[str]
    known: frozenset[str]


class SubtitleTag(IntEnum):
    """What a subtitle's filename says about its language, best first."""

    MINING = 0
    UNTAGGED = 1
    OTHER = 2


def _strip_retimed(stem: str) -> str:
    """*stem* without a Retime ``_retimed`` suffix, so ``ep01.ja_retimed`` still ends in its tag."""
    return stem[: -len(RETIMED_SUFFIX)] if stem.casefold().endswith(RETIMED_SUFFIX) else stem


def _tag_of(tokens: Iterable[str], language: SubtitleLanguage | None) -> SubtitleTag:
    """The first language token decides; a name with none is untagged."""
    if language is None:
        return SubtitleTag.UNTAGGED
    for token in tokens:
        if matches_language_tag(token, language.known):
            return SubtitleTag.MINING if matches_language_tag(token, language.mining) else SubtitleTag.OTHER
    return SubtitleTag.UNTAGGED


def _counts_as_tag(token: str, *, names_too: bool) -> bool:
    """A lowercase code (``ja``, ``pt-BR``), an all-caps three-letter one (``ENG``), or with
    *names_too* a spelled-out name (``English``).

    Never a capitalised word of three letters or fewer, nor an all-caps one of
    two: ``No. 6``, ``It``, ``ID``, ``All.In`` are titles, not Norwegian,
    Italian or Indonesian.
    """
    head = token.partition("-")[0]
    return head.islower() or (len(head) == 3 and head.isupper()) or (names_too and len(head) > 3)


def subtitle_language_tag(path: Path, language: SubtitleLanguage | None) -> SubtitleTag:
    """Read a language tag off a whole subtitle filename (``ep01.en.srt``, ``ep01.en.forced.srt``).

    A name made of nothing but tags and numbers (``ja.srt``, ``English.srt``,
    ``3_Japanese.srt``) is read whole. Otherwise the last two dot-separated
    parts of the stem are read, each up to a bracket (``ja[cc]``) and skipping
    flags such as ``sdh`` and ``forced``: a code counts in either, a
    spelled-out name only in the last (``Movie.2019.English``, not
    ``My.Big.Fat.Greek.Wedding``). Then a bracketed group closing the name
    (``EP01_track4_[eng]``). Release names capitalise their words, so
    ``All.In`` is not taken for Indonesian. A Retime ``_retimed`` suffix is
    dropped first.
    """
    if language is None:
        return SubtitleTag.UNTAGGED
    stem = _strip_retimed(path.stem)
    tokens = [token.strip("-") for token in _TAG_SPLIT.split(stem) if token.strip("-")]
    if tokens and all(
        token.isdigit()
        or token.casefold() in _FLAG_TOKENS
        or (_counts_as_tag(token, names_too=True) and matches_language_tag(token, language.known))
        for token in tokens
    ):
        return _tag_of(tokens, language)
    parts = [re.split(r"[\[(]", part, maxsplit=1)[0].strip() for part in stem.split(".")[1:]]
    parts = [part for part in parts if part.casefold() not in _FLAG_TOKENS][-2:]
    readable = [part for i, part in enumerate(parts) if _counts_as_tag(part, names_too=i == len(parts) - 1)]
    group = _TRAILING_GROUP.search(stem)
    if group is not None and _counts_as_tag(group.group(1).strip(), names_too=True):
        readable.append(group.group(1).strip())
    return _tag_of(readable, language)


def _tail_tag(tail: str, language: SubtitleLanguage | None) -> SubtitleTag:
    """Read a language tag off what follows the video's stem in a subtitle's name.

    Everything there is tags (``.ja``, ``.Japanese.forced``, ``_track3_[jpn]``,
    `` [eng]``), so every token counts, in any case. Spaces do not split, so an
    episode title in the tail (``- Kimi no Na wa``, ``- Just Do It``) stays one
    token and is never read as Norwegian or Italian; and the tail is read from
    the end, where tags sit.
    """
    tokens = [token.strip(" -") for token in _TAIL_SPLIT.split(_strip_retimed(tail))]
    return _tag_of(reversed(tokens), language)


def _is_forced(path: Path) -> bool:
    """Whether the name marks a forced track (``ep01.ja.forced.srt``): signs and foreign lines only."""
    return "forced" in _TAG_SPLIT.split(_strip_retimed(path.stem).casefold())


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


def find_sibling_subtitle(
    video_path: Path, priority: Sequence[str] | None = None, *, language: SubtitleLanguage | None = None
) -> Path | None:
    """Return the subtitle that belongs to *video_path*, or None when none clearly does.

    Searches the video's folder, then any ``Subs``-style folder beside it
    (:data:`_SUBTITLE_DIR_NAMES`), trying four rules in each, strictest first:

    1. Exact stem: ``EP01.srt`` or the Retime output ``EP01_retimed.srt``.
    2. Named for the video: the stem plus tags (``EP01.ja.srt``,
       ``Title [id].ja.vtt``), or any file in ``Subs/EP01/``.
    3. Same episode number (Batch's extractor and season rule), when no other
       video in the folder shares it.
    4. Only video in the folder: its only usable subtitle, unless that one
       names a different episode.

    Rules 1-2 run over every folder before rules 3-4 run over any, so a file
    named for the video in ``Subs/`` beats a guess beside it. A subtitle
    tagged for another language (*language*, from
    ``registry.subtitle_language``) is never returned. Among a rule's
    candidates :func:`_rank_key` decides; a tie returns None rather than
    guessing, and so does rule 1's case-ambiguity refusal.
    ``language=None`` reads every name as untagged.

    Args:
        video_path: Video (or media) file whose subtitle is sought.
        priority: Ordered lowercase extensions (e.g. ``(".ass", ".srt")``) to
            accept, best first.  Defaults to :data:`DEFAULT_SUBTITLE_PRIORITY`
            (``.ass > .ssa > .srt > .vtt > .smi``).  Callers may pass a narrower set to
            exclude a format, or a wider one to accept extras.
        language: The mining language's tags, or None for no preference.

    Matching is case-insensitive and NFC-normalized on names and extensions,
    so a ``.SRT`` or an NFD-encoded name is still found on case-sensitive
    filesystems. Reads are non-destructive, so the casefold here is
    unconditional (unlike the write-side resolver).
    """
    exts = DEFAULT_SUBTITLE_PRIORITY if priority is None else tuple(priority)
    folder = video_path.parent
    # WARNING, not silent: no sibling means the caller mines without a
    # subtitle, and an unreadable folder is indistinguishable from an empty one.
    files: list[Path] = []
    sub_dirs: list[Path] = []
    with suppressed(logger, f"scanning {folder} for a sibling subtitle", level=logging.WARNING):
        children = [p for p in folder.iterdir() if not is_junk_path(p.name)]
        files = [p for p in children if p.is_file()]
        sub_dirs = sorted(p for p in children if p.name.casefold() in _SUBTITLE_DIR_NAMES and p.is_dir())
    media_exts = FilePairMatcher.VIDEO_EXTENSIONS | _OTHER_MEDIA_EXTENSIONS | {video_path.suffix.lower()}
    video_key = _nfc(video_path.name).casefold()
    other_media = [p for p in files if p.suffix.lower() in media_exts and _nfc(p.name).casefold() != video_key]
    stem_cf = _nfc(video_path.stem).casefold()
    longer_stems = [s for s in (_nfc(m.stem).casefold() for m in other_media) if len(s) > len(stem_cf)]

    # Pass 1, rules 1-2 in every source; the folders scanned on the way are kept for pass 2.
    unnamed: list[list[Path]] = []
    for loose, per_video in _subtitle_sources(files, sub_dirs, stem_cf):
        subtitles = [p for p in loose if p.suffix.lower() in exts]
        decided, pick = _exact_stem_match(video_path, subtitles, exts)
        if decided:
            return pick
        named_dir = [p for p in per_video if p.suffix.lower() in exts]
        named = _named_candidates(stem_cf, longer_stems, subtitles, named_dir, language)
        decided, pick = _decide(named, exts)
        if decided:
            return _log_pick(video_path, pick, "named for the video")
        # Rule 2 read these as another language from the tags after the video's
        # name (``EP01_track4_[eng]``); the narrower whole-name reader of rules
        # 3-4 must not get a second look and call them untagged.
        other = {p for p, tag in named if tag is SubtitleTag.OTHER}
        unnamed.append([p for p in subtitles if p not in other])

    # Pass 2, rules 3-4: guesses from the episode number or the folder's shape.
    video_info = EpisodeNumberExtractor.extract_episode_info(video_path)
    for subtitles in unnamed:
        for rule, candidates in _guess_rules(video_info, other_media, subtitles, language):
            decided, pick = _decide(candidates, exts)
            if decided:
                return _log_pick(video_path, pick, rule)
    return None


def _log_pick(video_path: Path, pick: Path | None, rule: str) -> Path | None:
    """One INFO line for a looser rule's decision, so a surprising auto-fill is traceable in the log."""
    logger.info("subtitle auto-pair: %s -> %s (%s)", video_path.name, pick if pick is not None else "none, a tie", rule)
    return pick


def _subtitle_sources(files: list[Path], sub_dirs: list[Path], stem_cf: str) -> Iterator[tuple[list[Path], list[Path]]]:
    """Where a video's subtitles may sit, nearest first: (loose files, files in a folder named for it).

    The video's own folder first; then each ``Subs``-style folder beside it,
    paired with its ``<video stem>/`` subfolder when one exists. Scanned lazily,
    so a match in the video's folder never touches the others.
    """
    yield files, []
    for sub_dir in sub_dirs:
        loose: list[Path] = []
        per_video: list[Path] = []
        with suppressed(logger, f"scanning {sub_dir} for a sibling subtitle", level=logging.WARNING):
            inner = [p for p in sub_dir.iterdir() if not is_junk_path(p.name)]
            loose = [p for p in inner if p.is_file()]
            for named in (p for p in inner if _nfc(p.name).casefold() == stem_cf and p.is_dir()):
                per_video = [p for p in named.iterdir() if p.is_file() and not is_junk_path(p.name)]
        yield loose, per_video


def _exact_stem_match(video_path: Path, subtitles: list[Path], exts: Sequence[str]) -> tuple[bool, Path | None]:
    """Rule 1, ``<stem>.<ext>`` / ``<stem>_retimed.<ext>``: (decided, pick).

    Decided whenever any exact-stem file exists, including the ambiguous
    refusal (None), so the looser rules never override a file named exactly
    for the video. Within one extension the retimed group is tried first and an
    exact stem wins inside a group; multiple normalization-only matches are
    ambiguous rather than left to directory order. The retimed group is
    preferred ahead of the extension priority, so ``EP01_retimed.srt`` beats
    ``EP01.ass``.
    """
    stem_cf = _nfc(video_path.stem).casefold()
    retimed_cf = stem_cf + RETIMED_SUFFIX
    by_group: dict[tuple[bool, str], list[Path]] = {}
    for p in subtitles:
        p_stem_cf = _nfc(p.stem).casefold()
        if p_stem_cf == retimed_cf:
            by_group.setdefault((True, p.suffix.lower()), []).append(p)
        elif p_stem_cf == stem_cf:
            by_group.setdefault((False, p.suffix.lower()), []).append(p)
    for retimed in (True, False):
        wanted_stem = video_path.stem + RETIMED_SUFFIX if retimed else video_path.stem
        for ext in exts:
            candidates = by_group.get((retimed, ext), [])
            exact = next((p for p in candidates if p.stem == wanted_stem), None)
            if exact is not None:
                return True, exact
            if len(candidates) == 1:
                return True, candidates[0]
            if candidates:
                return True, None
    return False, None


def _same_episode(video: EpisodeInfo, other: EpisodeInfo | None) -> bool:
    """Batch's rule: one episode number, and one season when both name a season."""
    return (
        other is not None
        and video.episode_number == other.episode_number
        and (video.season_number is None or other.season_number is None or video.season_number == other.season_number)
    )


def _named_for(name_cf: str, stem_cf: str) -> bool:
    """Whether *name_cf* is *stem_cf* followed by tags (``ep01`` + ``.ja``); both casefolded."""
    return len(name_cf) > len(stem_cf) and name_cf.startswith(stem_cf) and name_cf[len(stem_cf)] in _TAG_SEPARATORS


def _named_candidates(
    stem_cf: str,
    longer_stems: list[str],
    subtitles: list[Path],
    named_dir: list[Path],
    language: SubtitleLanguage | None,
) -> list[tuple[Path, SubtitleTag]]:
    """Rule 2: the video's stem plus tags, or any file in ``Subs/<stem>/``.

    A name that a longer media stem beside the video also starts belongs to
    that media instead: ``Toy Story 2.ja.srt`` is not ``Toy Story``'s, and
    Condense's ``ep01_condensed.srt`` is not ``ep01``'s.
    """
    named: list[tuple[Path, SubtitleTag]] = []
    for p in subtitles:
        name_cf = _nfc(p.stem).casefold()
        if _named_for(name_cf, stem_cf) and not any(name_cf == s or _named_for(name_cf, s) for s in longer_stems):
            named.append((p, _tail_tag(name_cf[len(stem_cf) :], language)))
    named += [(p, subtitle_language_tag(p, language)) for p in named_dir]
    return named


def _guess_rules(
    video_info: EpisodeInfo | None,
    other_media: list[Path],
    subtitles: list[Path],
    language: SubtitleLanguage | None,
) -> Iterator[tuple[str, list[tuple[Path, SubtitleTag]]]]:
    """Rules 3-4 over one folder's subtitles, as (rule, candidates); rule 4 is computed only if reached."""
    if video_info is not None:
        rivals: list[EpisodeInfo] = []
        for m in other_media:
            info = EpisodeNumberExtractor.extract_episode_info(m)
            if info is not None and info.episode_number == video_info.episode_number:
                rivals.append(info)
        if not any(_same_episode(video_info, info) for info in rivals):
            same: list[tuple[Path, SubtitleTag]] = []
            for p in subtitles:
                info = EpisodeNumberExtractor.extract_episode_info(p)
                # Beside another season's episode N, a seasonless "- N" cannot say whose it is.
                if (
                    info is not None
                    and _same_episode(video_info, info)
                    and (not rivals or info.season_number is not None)
                ):
                    same.append((p, subtitle_language_tag(p, language)))
            yield "same episode number", same

    if not other_media:
        lone: list[tuple[Path, SubtitleTag]] = []
        for p in subtitles:
            info = EpisodeNumberExtractor.extract_episode_info(p)
            if video_info is None or info is None or _same_episode(video_info, info):
                lone.append((p, subtitle_language_tag(p, language)))
        yield "only video in the folder", lone


def _decide(candidates: list[tuple[Path, SubtitleTag]], exts: Sequence[str]) -> tuple[bool, Path | None]:
    """The best of one rule's candidates: (decided, pick).

    Another language never pairs. A rule left with nothing defers to the next;
    a rule whose best two tie decides None, since a looser rule cannot know better.
    """
    keyed = sorted(
        (
            (_rank_key(path, tag, exts, prefer_retimed=True), path)
            for path, tag in candidates
            if tag is not SubtitleTag.OTHER
        ),
        key=lambda item: item[0],
    )
    if not keyed:
        return False, None
    if len(keyed) > 1 and keyed[0][0] == keyed[1][0]:
        return True, None
    return True, keyed[0][1]


def _rank_key(
    path: Path, tag: SubtitleTag, exts: Sequence[str], prefer_retimed: bool
) -> tuple[bool, bool, int, bool, int]:
    """How good *path* is as its video's subtitle; smallest first.

    Another language sorts last, below even the retime of one. Then a retime
    beats the original it was made from (mining the off-timed file would undo
    it), a mining-language tag beats none, a full track beats a forced one,
    and the format priority settles the rest.
    """
    suffix = path.suffix.lower()
    return (
        tag is SubtitleTag.OTHER,
        not (prefer_retimed and _is_retimed(path)),
        int(tag),
        _is_forced(path),
        exts.index(suffix) if suffix in exts else len(exts),
    )


def _sort_subtitles(subtitles: list[Path], prefer_retimed: bool, language: SubtitleLanguage | None = None) -> None:
    """Order subtitle candidates in place, best-match-first for episode pairing.

    Pure ordering, no I/O: the caller owns the scan and its one warning. Applied
    to the mining track and to the secondary-language track (F7) alike; only the
    mining track passes *language*, since a translation folder holds another
    language on purpose.
    """
    subtitles.sort(
        key=lambda subtitle: (
            *_rank_key(subtitle, subtitle_language_tag(subtitle, language), DEFAULT_SUBTITLE_PRIORITY, prefer_retimed),
            subtitle.suffix.lower(),
            _nfc(subtitle.name),
            # NFC collapses canonically equivalent spellings to one key; the
            # raw name makes the order total so iterdir() order can't decide.
            subtitle.name,
        )
    )


def is_same_folder(a: Path, b: Path) -> bool:
    """Whether two folder paths name one directory, literally or once resolved."""
    return a == b or a.resolve() == b.resolve()


def _files_with_suffix(folder: Path, exts: Collection[str]) -> list[Path]:
    """Files directly in *folder* whose lowercased suffix is in *exts*, skipping junk siblings."""
    return [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts and not is_junk_path(f.name)]


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
    if is_same_folder(secondary_folder, subtitle_folder):
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
        secondary_subs = _files_with_suffix(secondary_folder, subtitle_exts)
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
    """Pairs a folder of videos with a folder of subtitles by episode number."""

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
        language: SubtitleLanguage | None = None,
    ) -> list[FilePair]:
        """Find matching pairs by episode number instead of exact name.

        Matches files like:
        - Jujutsu_Kaisen_01.mp4 ↔ jjk_ep01.ass (both episode 1)
        - S01E05.mkv ↔ 05.srt (both episode 5)
        - video_1.mp4 ↔ episode_01.ass (both episode 1, different padding)

        The show name is discarded, so a folder holds one show (Issue #39):
        two shows sharing episode numbers in one folder is unsupported by
        design. Name-based matching was deleted; every batch path routes
        through here (batch_processing_tab, batch_queue_worker, queue_panel,
        subtitle_retime_tab, condense_tab), so don't bring
        it back without rewiring all five.

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
            language: Mining language's tags (``registry.subtitle_language``).
                When two subtitles share an episode, a mining-language tag
                beats an untagged file, which beats another language's.
                Ranking only: nothing is dropped. ``None`` keeps name order.

        Returns:
            List of FilePair objects matched by episode number
        """
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
        # is_junk_path: a macOS AppleDouble sidecar (``._EP01.srt``) keeps the
        # real file's extension and sorts ahead of it, so it would take the
        # episode number and the real subtitle would be skipped as a collision.
        videos: list[Path] = []
        subtitles: list[Path] = []
        with suppressed(logger, f"scanning {video_folder} and {subtitle_folder} for pairs", level=logging.WARNING):
            videos = _files_with_suffix(video_folder, video_exts)
            subtitles = _files_with_suffix(subtitle_folder, subtitle_exts)
        if not videos or not subtitles:
            return []

        # Deterministic video order: iterdir() order is filesystem-dependent, and
        # when episode extraction collapses several videos onto one number the
        # match outcome would otherwise depend on directory enumeration order
        # while the subtitle side is fully sorted — a shuffle that silently pairs
        # episode N's subtitle with episode M's video.
        videos.sort(key=lambda video: (_nfc(video.name), video.name))
        _sort_subtitles(subtitles, prefer_retimed, language)

        # Match by episode number
        matched_pairs = EpisodeMatcher.match_by_episode_number(videos, subtitles)

        # Convert to FilePair objects
        pairs = [FilePair(video, subtitle) for video, subtitle in matched_pairs]
        if secondary_folder is not None:
            _attach_secondary(pairs, videos, secondary_folder, subtitle_folder, subtitle_exts, prefer_retimed)
        return pairs

"""Episode number extraction and matching for video/subtitle pairs."""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EpisodeInfo:
    """Information extracted from episode filename."""

    file_path: Path
    episode_number: int
    season_number: int | None = None

    @property
    def filename(self) -> str:
        """Get filename."""
        return self.file_path.name


def _strip_technical_tokens(name: str) -> str:
    """Strip technical metadata that confuses episode-number regexes.

    Resolution tokens like "1280x720" otherwise get parsed as
    season=1280, episode=720 by the NxN pattern (Issue #36).
    """
    name = re.sub(r"\d{3,4}[xX]\d{3,4}", "", name)
    # Use explicit non-alphanumeric boundaries rather than \b: \b does not
    # fire between an underscore and a digit (both are word chars), so
    # "Show_03_720p" kept "720p" — which the old consuming BARE_NUMBER regex
    # silently skipped but the lookahead form would mine as episode 720.
    name = re.sub(r"(?<![0-9A-Za-z])\d{3,4}[pi](?![0-9A-Za-z])", "", name, flags=re.IGNORECASE)
    # Strip release-encoding tags whose embedded digits otherwise win the
    # trailing-number fallback (Issue #80): video codec (x264/x265/h264/
    # h265/av1/vp9), color bit-depth (10-bit/8bit), and the 8-hex CRC32
    # checksum fansub groups append, e.g. "[3EEAABE6]". In the standard
    # "[Group] Title - 03 - EpTitle [BD 1080p x265 10-bit][3EEAABE6]" format
    # the real episode ("- 03 -") otherwise loses to "265", "10", or a
    # checksum digit, collapsing distinct episodes onto one number and
    # mispairing them. All three strips are required: any one left in leaves
    # a different trailing junk number that re-triggers the collision.
    name = re.sub(r"(?<![0-9A-Za-z])(?:[xh]\.?26[45]|av1|vp9)(?![0-9A-Za-z])", "", name, flags=re.IGNORECASE)
    name = re.sub(r"(?<![0-9A-Za-z])\d{1,2}[\s._-]?bit(?![0-9A-Za-z])", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[\[(][0-9A-Fa-f]{8}[\])]", "", name)
    return name


class EpisodeNumberExtractor:
    """Extract episode numbers from filenames using regex patterns."""

    # Just numbers: 01, 001, 1 (at boundaries or after non-digits). Last-resort
    # fallback handled separately (findall + LAST match) so numeric show titles
    # like "86", "Mob Psycho 100", "Steins;Gate 0", "3-gatsu" don't steal the
    # episode slot from the trailing episode number. See extract_episode_info.
    #
    # The trailing boundary is a LOOKAHEAD, not a consuming class: consuming the
    # separator made non-overlapping findall skip a number that immediately
    # followed a single-char-separated number ("Title 1 2" -> ["1"], "5-6-7" ->
    # ["5","7"]), so bare[-1] picked the wrong episode. The lookahead keeps the
    # separator available to start the next match, so every run is captured.
    BARE_NUMBER = r"(?:^|[^\d])(\d{1,3})(?=[^\d]|$)"

    # Regex patterns for common episode naming conventions (in priority order).
    # Each is tried with re.search (FIRST match) before falling back to
    # BARE_NUMBER. The bare-number fallback is intentionally excluded here.
    PATTERNS = [
        # S01E01, s1e1, S01 E01, S01.E01, S01-E01 (season + episode). The
        # separator class lets "Show.S02 E05" be read as season 2 / episode 5
        # instead of falling through to BARE_NUMBER and mining the season.
        (r"[Ss](\d+)[\s._-]*[Ee](\d+)", lambda m: (int(m.group(1)), int(m.group(2)))),
        # Fansub release slot: "Title - 01v2 [1080p]" or "Title - 01 - Name".
        # The following delimiter is required so an internal title fragment such
        # as "- 5 Centimeters" cannot steal the episode slot.
        (r"\s+-\s+(\d{1,4})(?:[vV]\d+)?(?=\s*(?:-|[\[(]|$))", lambda m: (None, int(m.group(1)))),
        # 1x01, 1X01 (season x episode)
        (r"(\d+)[xX](\d+)", lambda m: (int(m.group(1)), int(m.group(2)))),
        # Episode 01, Ep01, ep.01, episode_01 (no season)
        (
            r"(?<![0-9A-Za-z])[Ee][Pp](?:isode)?[\s._-]*(\d+)(?![0-9A-Za-z])",
            lambda m: (None, int(m.group(1))),
        ),
    ]

    @classmethod
    def extract_episode_info(cls, file_path: Path) -> EpisodeInfo | None:
        """Extract episode number from filename.

        Args:
            file_path: Path to video or subtitle file

        Returns:
            EpisodeInfo if episode number found, None otherwise
        """
        filename = _strip_technical_tokens(file_path.stem)

        for pattern, extractor in cls.PATTERNS:
            match = re.search(pattern, filename)
            if match:
                season, episode = extractor(match)
                return EpisodeInfo(file_path, episode, season)

        # A number attached to an embedded "ep" token belongs to the ordinary
        # word, not the episode marker (for example, "Step 3"). Prefer the
        # remaining candidates over it — but when it holds the ONLY number in
        # the name ("Step_03.mkv"), it is also the only episode candidate, so
        # fall back to the unsuppressed name rather than extracting nothing.
        suppressed = re.sub(
            r"(?<=[0-9A-Za-z])[Ee][Pp](?:isode)?[\s._-]*\d+(?![0-9A-Za-z])",
            "",
            filename,
        )

        # Last resort: bare number. Take the LAST 1-3 digit run, not the first —
        # numeric show titles ("86 - 03", "Mob Psycho 100 - 05") put the title
        # number first and the episode number last; taking the first collapsed
        # every file in the folder onto the title number (T-04).
        bare = re.findall(cls.BARE_NUMBER, suppressed) or re.findall(cls.BARE_NUMBER, filename)
        if bare:
            return EpisodeInfo(file_path, int(bare[-1]), None)

        return None


class EpisodeMatcher:
    """Match video/subtitle files by episode number."""

    @staticmethod
    def match_by_episode_number(video_files: list[Path], subtitle_files: list[Path]) -> list[tuple[Path, Path]]:
        """Match video and subtitle files by episode number.

        Matches:
        - Same episode number (1 ↔ 01)
        - If both have season, seasons must match too

        Args:
            video_files: List of video file paths
            subtitle_files: List of subtitle file paths

        Returns:
            List of (video, subtitle) tuples sorted by episode number
        """
        # Extract episode info for all files
        video_episodes = []
        for video in video_files:
            info = EpisodeNumberExtractor.extract_episode_info(video)
            if info:
                video_episodes.append(info)

        subtitle_episodes = []
        for subtitle in subtitle_files:
            info = EpisodeNumberExtractor.extract_episode_info(subtitle)
            if info:
                subtitle_episodes.append(info)

        # Match by episode number. A subtitle is consumed once and never reused:
        # without this, multiple videos sharing an episode number (multiple shows
        # in one folder) all collapse onto the first matching subtitle (Issue #39).
        # Prefer an explicit same-season match before a seasonless fallback.
        pairs = []
        used_subtitles: set[Path] = set()
        for video_info in video_episodes:
            subtitle_candidates = sorted(
                subtitle_episodes,
                key=lambda candidate: (
                    not (video_info.season_number is not None and candidate.season_number == video_info.season_number)
                ),
            )
            for subtitle_info in subtitle_candidates:
                if subtitle_info.file_path in used_subtitles:
                    continue
                # Match if episode numbers are the same
                if video_info.episode_number == subtitle_info.episode_number:
                    # If both have season numbers, they must match
                    if (
                        video_info.season_number is not None
                        and subtitle_info.season_number is not None
                        and video_info.season_number != subtitle_info.season_number
                    ):
                        continue  # Seasons don't match, skip

                    pairs.append((video_info.file_path, subtitle_info.file_path, video_info.episode_number))
                    used_subtitles.add(subtitle_info.file_path)
                    break  # Found match, move to next video

        # Sort by episode number using cached value
        pairs.sort(key=lambda p: p[2])
        # Return without the cached episode number
        return [(p[0], p[1]) for p in pairs]


_BRACKET_GROUP = re.compile(r"\[[^\]]*\]")
_SOURCE_TOKENS = re.compile(
    r"\b(?:WEB(?:-?DL|Rip)?|Blu-?Ray|BD(?:Rip)?|HDTV|DVDRip|AMZN|NF|CR)\b",
    re.IGNORECASE,
)
# Space before the dash is required so in-word hyphens ("Re-Start") survive.
_RELEASE_GROUP_TAIL = re.compile(r"\s-\s*\w+\s*$")
_EDGE_SEPARATORS = " -._"


@dataclass(frozen=True)
class ParsedMediaName:
    """Series/episode fields parsed from a release-style media filename."""

    series: str | None
    season: int | None
    episode: int | None
    episode_title: str | None


def parse_media_filename(path: Path) -> ParsedMediaName:
    """Parse series / season / episode / episode-title from a media filename.

    Heuristic, for pre-filling editable metadata fields (Issue #113) — a miss
    returns None fields, never raises. Reuses ``EpisodeNumberExtractor.PATTERNS``
    for the episode marker and splits series (before the marker) from episode
    title (after it, with release tags stripped).
    """
    stem = path.stem
    # Scene-style names separate words with dots/underscores instead of spaces.
    if " " not in stem and re.search(r"[._]", stem):
        stem = re.sub(r"[._]+", " ", stem)
    cleaned = _BRACKET_GROUP.sub(" ", stem)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    for pattern, extractor in EpisodeNumberExtractor.PATTERNS:
        match = re.search(pattern, cleaned)
        if match:
            season, episode = extractor(match)
            series = cleaned[: match.start()].strip(_EDGE_SEPARATORS)
            title = _clean_episode_title(cleaned[match.end() :])
            return ParsedMediaName(series or None, season, episode, title or None)

    info = EpisodeNumberExtractor.extract_episode_info(path)
    if info is not None:
        return ParsedMediaName(None, info.season_number, info.episode_number, None)
    return ParsedMediaName(None, None, None, None)


def _clean_episode_title(tail: str) -> str:
    tail = _strip_technical_tokens(tail)
    tail = _SOURCE_TOKENS.sub(" ", tail)
    tail = re.sub(r"\(\s*\)", " ", tail)  # parens emptied by the strips above
    tail = re.sub(r"\s{2,}", " ", tail).strip(_EDGE_SEPARATORS)
    tail = _RELEASE_GROUP_TAIL.sub("", tail)
    return tail.strip(_EDGE_SEPARATORS)

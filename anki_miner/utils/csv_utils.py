"""Shared CSV helpers for delimiter detection and header row identification."""

import re

# Common header keywords that indicate a header row (case-insensitive)
_HEADER_KEYWORDS = {
    "word",
    "rank",
    "frequency",
    "freq",
    "lemma",
    "reading",
    "kana",
    "kanji",
}


#: One ``word count`` line: a whitespace-free token, one space, digits — the
#: hermitdave/FrequencyWords shape, which carries no header and no tab/comma.
_WORD_COUNT_LINE = re.compile(r"\S+ \d+")


def _is_word_count_sample(sample: str) -> bool:
    """Whether every complete line of *sample* is a ``word count`` pair.

    A bounded read (the importer sniffs 4 KB) can cut the last line mid-way, so
    a final line with no line break is ignored when there are others.
    """
    lines = sample.splitlines()
    if len(lines) > 1 and not sample.endswith(("\n", "\r")):
        lines = lines[:-1]
    lines = [line.rstrip() for line in lines if line.strip()]
    return bool(lines) and all(_WORD_COUNT_LINE.fullmatch(line) for line in lines)


def detect_delimiter(sample: str, *, prefer_tab: bool = False) -> str:
    """Detect whether a file uses tab, comma or a single space as delimiter.

    A sample with neither tab nor comma whose every line is ``word count`` is
    space-delimited (hermitdave OpenSubtitles lists); anything else keeps the
    tab/comma tie-break.

    Args:
        sample: First few lines of the file.
        prefer_tab: Choose tab whenever one is present. Pitch rows use this
            because commas are valid inside their pattern field.

    Returns:
        Detected delimiter character.
    """
    tab_count = sample.count("\t")
    if prefer_tab and tab_count:
        return "\t"
    comma_count = sample.count(",")
    if not tab_count and not comma_count and _is_word_count_sample(sample):
        return " "
    return "\t" if tab_count > comma_count else ","


def is_header_row(row: list[str]) -> bool:
    """Check if a row looks like a header based on common keywords."""
    return any(cell.strip().lower() in _HEADER_KEYWORDS for cell in row)

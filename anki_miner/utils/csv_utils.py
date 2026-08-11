"""Shared CSV helpers for delimiter detection and header row identification."""

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


def detect_delimiter(sample: str, *, prefer_tab: bool = False) -> str:
    """Detect whether a file uses tab or comma as delimiter.

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
    return "\t" if tab_count > sample.count(",") else ","


def is_header_row(row: list[str]) -> bool:
    """Check if a row looks like a header based on common keywords."""
    return any(cell.strip().lower() in _HEADER_KEYWORDS for cell in row)

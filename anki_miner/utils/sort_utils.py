"""Sorting utilities, especially for natural sorting."""

import re
from typing import Any


def natural_sort_key(text: str) -> list[Any]:
    """Generate a natural sort key for a string.

    Natural sorting treats numbers numerically rather than alphabetically.
    For example: file1, file2, file10 instead of file1, file10, file2

    Args:
        text: String to generate sort key for

    Returns:
        List of strings and integers for sorting

    Example:
        files = ["file10.txt", "file2.txt", "file1.txt"]
        sorted(files, key=natural_sort_key)
        # Returns: ["file1.txt", "file2.txt", "file10.txt"]
    """

    def convert(text_segment):
        return int(text_segment) if text_segment.isdigit() else text_segment.lower()

    return [convert(c) for c in re.split(r"(\d+)", str(text))]

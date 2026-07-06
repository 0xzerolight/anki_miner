"""Shared pure-stdlib helpers for the reading-tab source loaders."""

from __future__ import annotations

import re

# Listings junk dropped from both directory walks and archive namelists so the
# two paths filter identically (see is_junk_path). __MACOSX and $RECYCLE.BIN are
# directory components; .DS_Store and Thumbs.db are files.
JUNK_NAMES: frozenset[str] = frozenset({"__MACOSX", ".DS_Store", "Thumbs.db", "$RECYCLE.BIN"})

_NUM_RE = re.compile(r"(\d+)")


def natural_sort_key(s: str) -> list[int | str]:
    """Classic natural-sort key: digit runs compare numerically.

    Splitting on a captured ``(\\d+)`` yields alternating text/number chunks;
    numeric chunks are int-cast so "Vol2" sorts before "Vol10".
    """
    return [int(chunk) if chunk.isdigit() else chunk for chunk in _NUM_RE.split(s)]


def is_junk_path(name: str) -> bool:
    """True when any path component is OS/archive listing junk.

    Accepts a bare name or a ``/``- (or ``\\``-) separated path; matches junk
    in nested components too, e.g. ``foo/__MACOSX/bar.jpg``.
    """
    return any(part in JUNK_NAMES for part in name.replace("\\", "/").split("/") if part)

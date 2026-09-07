"""Parse the playlist picker's index expression.

Deliberately the same shape as yt-dlp's own ``--playlist-items``: a user who
already knows ``1-10,15`` types it and it works. Unlike yt-dlp's, this one is
1-based only and rejects a single index past the end rather than silently
ignoring it — the picker knows exactly how many entries it holds, so a typo is
worth reporting instead of quietly selecting fewer videos than the user asked
for. An open-ended *range* still clamps, because "8-" plainly means "the rest".
"""

from __future__ import annotations

import re

_PART_RE = re.compile(r"^(\d*)(-?)(\d*)$")


def parse_index_selection(spec: str, total: int) -> set[int]:
    """Return the 1-based indices *spec* names, within ``1..total``.

    ``"1-3,7"`` -> ``{1, 2, 3, 7}``. An open end takes the boundary: ``"8-"``
    runs to *total*, ``"-3"`` starts at 1. A reversed range is normalised, and a
    range end past *total* is clamped. A blank *spec* selects nothing.

    Raises:
        ValueError: a part is not a number or a range, names index 0, or names a
            single index past *total*.
    """
    selected: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip().replace(" ", "")
        if not part:
            continue
        match = _PART_RE.match(part)
        if match is None:
            raise ValueError(f"Not a number or a range: {raw_part.strip()}")
        start_text, dash, end_text = match.groups()
        if not dash:
            if not start_text:
                raise ValueError(f"Not a number or a range: {raw_part.strip()}")
            index = int(start_text)
            if not 1 <= index <= total:
                raise ValueError(f"There is no video {index}.")
            selected.add(index)
            continue
        if not start_text and not end_text:
            raise ValueError("A range needs at least one end.")
        start = int(start_text) if start_text else 1
        end = int(end_text) if end_text else total
        if start == 0 or end == 0:
            raise ValueError("Videos are numbered from 1.")
        if start > end:
            start, end = end, start
        selected.update(range(max(start, 1), min(end, total) + 1))
    return selected

"""Parse the playlist picker's index expression.

Deliberately the same shape as yt-dlp's ``--playlist-items``, including its
``a:b`` spelling: a user who already knows ``1-10,15`` or ``1:10,15`` types it
and it works. Unlike yt-dlp's, this one is 1-based only and rejects a single
index past the end rather than silently ignoring it — the picker knows
exactly how many entries it holds, so a typo is worth reporting instead of
quietly selecting fewer videos than the user asked for. An open-ended *range*
still clamps, because "8-" plainly means "the rest".
"""

from __future__ import annotations

import re

_PART_RE = re.compile(r"^(\d*)([-:]?)(\d*)$")


class SelectionError(ValueError):
    """A malformed selection part.

    ``kind`` is a stable key the dialog maps to a translated sentence — this
    module is Qt-free, so it cannot translate; ``value`` is the offending part.
    ``str(exc)`` stays English for logs and tests.
    """

    def __init__(self, kind: str, value: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.value = value


def parse_index_selection(spec: str, total: int, *, first: int = 1) -> set[int]:
    """Return the indices *spec* names, within ``first..first + total - 1``.

    ``"1-3,7"`` -> ``{1, 2, 3, 7}``. *first* is the number printed on the first
    row, so a later playlist page (501…) is selected with the numbers the user
    sees. An open end takes the boundary: ``"8-"`` runs to the last index,
    ``"-3"`` starts at *first*. A reversed range is normalised, and a range end
    outside the page is clamped. A blank *spec* selects nothing.

    Raises:
        ValueError: a part is not a number or a range, names index 0, or names a
            single index outside the page.
    """
    last = first + total - 1
    selected: set[int] = set()
    for raw_part in spec.split(","):
        part_text = raw_part.strip()
        part = part_text.replace(" ", "")
        if not part:
            continue
        match = _PART_RE.match(part)
        if match is None:
            raise SelectionError("not_a_range", part_text, f"Not a number or a range: {part_text}")
        start_text, sep, end_text = match.groups()
        if not sep:
            if not start_text:
                raise SelectionError("not_a_range", part_text, f"Not a number or a range: {part_text}")
            index = int(start_text)
            if not first <= index <= last:
                raise SelectionError("no_such_video", part_text, f"There is no video {index}.")
            selected.add(index)
            continue
        if not start_text and not end_text:
            raise SelectionError("open_range", part_text, "A range needs at least one end.")
        start = int(start_text) if start_text else first
        end = int(end_text) if end_text else last
        if start == 0 or end == 0:
            raise SelectionError("from_one", part_text, "Videos are numbered from 1.")
        if start > end:
            start, end = end, start
        selected.update(range(max(start, first), min(end, last) + 1))
    return selected

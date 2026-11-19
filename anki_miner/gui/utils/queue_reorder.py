"""Alt+Up / Alt+Down for a queue list that a filter or search may narrow."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def move_past_visible(order: Sequence[T], visible: Sequence[T], selected: Sequence[T], delta: int) -> list[T] | None:
    """*order* with each selected row moved one VISIBLE row up (-1) or down (+1).

    Each selected row is placed just past the nearest unselected visible row in
    the direction of travel, so the list on screen is the list the keyboard
    reorders: a hidden neighbour is skipped, not swapped with, and the other
    rows (hidden ones included) keep their places. None when nothing is
    selected or the leading selected row is already at the visible edge.
    """
    picked = {id(item) for item in selected}
    shown = {id(item) for item in visible}
    seq = list(order) if delta < 0 else list(reversed(order))
    moving = [item for item in seq if id(item) in picked]
    if not moving:
        return None
    for item in moving:
        pos = next(i for i, x in enumerate(seq) if x is item)
        target = next(
            (i for i in range(pos - 1, -1, -1) if id(seq[i]) in shown and id(seq[i]) not in picked),
            None,
        )
        if target is None:
            return None
        seq.insert(target, seq.pop(pos))
    return seq if delta < 0 else seq[::-1]

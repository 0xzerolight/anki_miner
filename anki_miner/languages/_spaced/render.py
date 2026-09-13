"""Card render hooks shared by every spaCy language (spec §4.8)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from anki_miner.languages._spaced.pos import UPOS_LABELS

if TYPE_CHECKING:  # annotation-only, the ko/render.py pattern
    from anki_miner.config.config import AnkiMinerConfig


class PosHook:
    """``pos``: the word's universal POS as a lowercase English label."""

    def __init__(self, labels: Mapping[str, str] = UPOS_LABELS) -> None:
        self._labels = labels

    def field_names(self) -> tuple[str, ...]:
        return ("pos",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # mapped field name is the switch; no setting gates it
        label = self._labels.get(str(getattr(word, "pos", "") or ""), "")
        return {"pos": label.lower()} if label else {}

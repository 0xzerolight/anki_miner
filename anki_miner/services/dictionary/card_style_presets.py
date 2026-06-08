"""Card-style preset registry (card-style presets feature).

A single source of truth for the named CSS "looks" a user can pick for the
dictionary glossary HTML. Each preset maps a stable ``id`` to a bundled
stylesheet under :mod:`anki_miner.services.dictionary.resources.presets`.

Purely additive infrastructure: no Qt, no I/O at import time. The CSS text is
read lazily on demand via :func:`load_preset_css`. The ``"none"`` preset (and
any unknown id) resolves to ``""`` so callers can compose it with custom CSS
without special-casing.
"""

from __future__ import annotations

from importlib.resources import files
from typing import NamedTuple

_PRESET_PACKAGE = "anki_miner.services.dictionary.resources.presets"

#: Id of the preset used when none has been chosen.
DEFAULT_PRESET_ID = "default"


class CardStylePreset(NamedTuple):
    """A selectable card-style preset.

    ``filename`` is ``None`` for the sentinel "none" entry, which carries no
    bundled stylesheet (custom CSS only).
    """

    id: str
    display_name: str
    filename: str | None


#: Ordered, immutable registry of presets. Order is the GUI presentation order.
PRESETS: tuple[CardStylePreset, ...] = (
    CardStylePreset("default", "Default", "default.css"),
    CardStylePreset("yomitan-classic", "Yomitan / Lapis Classic", "yomitan-classic.css"),
    CardStylePreset("minimal", "Minimal / Clean", "minimal.css"),
    CardStylePreset("none", "None (custom CSS only)", None),
)

_BY_ID: dict[str, CardStylePreset] = {p.id: p for p in PRESETS}


def load_preset_css(preset_id: str) -> str:
    """Return the bundled CSS text for ``preset_id``.

    Returns ``""`` for the ``"none"`` preset and for any unknown id (no
    exception), so callers can treat a missing preset as "no managed CSS".
    """
    preset = _BY_ID.get(preset_id)
    if preset is None or preset.filename is None:
        return ""
    return files(_PRESET_PACKAGE).joinpath(preset.filename).read_text(encoding="utf-8")

"""Card-style preset registry (card-style presets feature).

A single source of truth for the named CSS "looks" a user can pick for the
dictionary glossary HTML. Each preset maps a stable ``id`` to a bundled
stylesheet under :mod:`anki_miner.services.dictionary.resources.presets`.

Purely additive infrastructure: no Qt, no I/O at import time. The CSS text is
read lazily on demand via :func:`load_preset_css`. The ``"off"`` and ``"none"``
presets (and any unknown id) resolve to ``""`` so callers can compose them with
custom CSS without special-casing.

Two sentinel presets carry no bundled stylesheet:

- ``"off"`` (:data:`OFF_PRESET_ID`) means "don't manage this note type's styling
  at all" — the Settings sync strips Anki Miner's managed block. This is the
  default for fresh installs (opt-in).
- ``"none"`` means "managed block with only the user's custom CSS, no bundled
  preset".

Every preset rule is guarded by miner-only ``ol[data-count]`` markup so the
CSS never restyles Yomitan-exported glossary HTML in a shared note type
(double indentation); enforced by ``TestPresetYomitanLeak``.
"""

from __future__ import annotations

from importlib.resources import files
from typing import NamedTuple

_PRESET_PACKAGE = "anki_miner.services.dictionary.resources.presets"

#: Id of the bundled "Default" stylesheet preset.
DEFAULT_PRESET_ID = "default"

#: Id of the "Off" sentinel — styling is not managed; the managed block is
#: stripped from the note type. Default for fresh installs.
OFF_PRESET_ID = "off"


class CardStylePreset(NamedTuple):
    """A selectable card-style preset.

    ``filename`` is ``None`` for the sentinel "off" and "none" entries, which
    carry no bundled stylesheet.
    """

    id: str
    display_name: str
    filename: str | None


#: Ordered, immutable registry of presets. Order is the GUI presentation order;
#: "Off" leads so the dropdown's first entry is the un-styled / opt-out state.
PRESETS: tuple[CardStylePreset, ...] = (
    CardStylePreset(OFF_PRESET_ID, "Off", None),
    CardStylePreset("default", "Default", "default.css"),
    CardStylePreset("yomitan-classic", "Yomitan / Lapis Classic", "yomitan-classic.css"),
    CardStylePreset("minimal", "Minimal / Clean", "minimal.css"),
    CardStylePreset("none", "Custom CSS only", None),
)

_BY_ID: dict[str, CardStylePreset] = {p.id: p for p in PRESETS}


def load_preset_css(preset_id: str) -> str:
    """Return the bundled CSS text for ``preset_id``.

    Returns ``""`` for the ``"off"`` / ``"none"`` presets and for any unknown id
    (no exception), so callers can treat a missing preset as "no managed CSS".
    """
    preset = _BY_ID.get(preset_id)
    if preset is None or preset.filename is None:
        return ""
    return files(_PRESET_PACKAGE).joinpath(preset.filename).read_text(encoding="utf-8")

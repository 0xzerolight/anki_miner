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

#: Generic structured-content fallback appended to every file-backed preset
#: (Issue #87). Styles the common Yomitan ``data-sc-*`` hooks so dictionaries
#: that ship no ``styles.css`` still render; the leading underscore keeps it out
#: of the user-selectable :data:`PRESETS` registry.
_SHARED_PARTIAL_FILENAME = "_structured_content.css"

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
#: Two real "looks": ``default`` (Rich — full chrome) and ``minimal`` (no chrome).
PRESETS: tuple[CardStylePreset, ...] = (
    CardStylePreset(OFF_PRESET_ID, "Off", None),
    CardStylePreset("default", "Rich", "default.css"),
    CardStylePreset("minimal", "Minimal", "minimal.css"),
    CardStylePreset("none", "Custom CSS only", None),
)

_BY_ID: dict[str, CardStylePreset] = {p.id: p for p in PRESETS}

#: Retired preset ids mapped onto their closest surviving look. ``yomitan-classic``
#: was folded into the rebuilt ``default`` (Rich). Applied to both saved-config ids
#: and the ``preset=<id>`` recorded in a note type's live managed block, so existing
#: users keep their styling instead of silently dropping to "Off".
LEGACY_PRESET_ALIASES: dict[str, str] = {
    "yomitan-classic": "default",
}


def resolve_preset_alias(preset_id: str) -> str:
    """Map a retired preset id onto its surviving replacement.

    Returns ``preset_id`` unchanged when it is not a retired alias (including the
    empty string used for legacy markers and any unknown id — coercion of those is
    the caller's concern, not this function's).
    """
    return LEGACY_PRESET_ALIASES.get(preset_id, preset_id)


def load_preset_css(preset_id: str) -> str:
    """Return the bundled CSS text for ``preset_id``.

    The preset's own chrome stylesheet is followed by the shared generic
    structured-content fallback (:data:`_SHARED_PARTIAL_FILENAME`), so both the
    "Rich" and "Minimal" looks style dictionary content (tag pills, note boxes,
    inflection tables) even for dicts that ship no ``styles.css`` (Issue #87).

    Returns ``""`` for the ``"off"`` / ``"none"`` presets and for any unknown id
    (no exception), so callers can treat a missing preset as "no managed CSS".
    """
    preset = _BY_ID.get(preset_id)
    if preset is None or preset.filename is None:
        return ""
    package = files(_PRESET_PACKAGE)
    chrome = package.joinpath(preset.filename).read_text(encoding="utf-8")
    fallback = package.joinpath(_SHARED_PARTIAL_FILENAME).read_text(encoding="utf-8")
    return f"{chrome}\n{fallback}"

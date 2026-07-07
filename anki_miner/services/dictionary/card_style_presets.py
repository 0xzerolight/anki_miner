"""Universal glossary stylesheet loader.

Anki Miner ships one bundled stylesheet — :mod:`resources.glossary.css` — that is
the always-on base for mined-card glossary HTML. There are no selectable presets:
the look is universal, and per-dictionary author CSS is composed after it by
:func:`card_style_block.build_card_style_block` into a self-contained ``<style>``
block embedded in each card.

No Qt, no I/O at import time; the CSS text is read lazily via :func:`load_glossary_css`.
"""

from __future__ import annotations

from importlib.resources import files

_RESOURCE_PACKAGE = "anki_miner.services.dictionary.resources"
_GLOSSARY_FILENAME = "glossary.css"


def load_glossary_css() -> str:
    """Return the bundled universal glossary stylesheet text."""
    return files(_RESOURCE_PACKAGE).joinpath(_GLOSSARY_FILENAME).read_text(encoding="utf-8")

"""Can this machine mine Turkish? (the profile's ``unavailable_reason``).

One hard requirement, zeyrek: an importable package (a pip install with the ``tr`` extra) OR the Turkish pack on
disk, whose other components (nltk, regex, defusedxml, colorama) always install with it. ``find_spec`` answers
without importing, through the spaCy languages' own probe helpers.
"""

from __future__ import annotations

import sys

from anki_miner.languages._spaced.availability import _importable, _pack_component_present

TR_ENGINE = "zeyrek"


def tr_missing_reason() -> str | None:
    if _importable(TR_ENGINE) or _pack_component_present("tr", TR_ENGINE):
        return None
    if getattr(sys, "frozen", False):
        return "Turkish mining needs the Turkish language pack. Download it in Settings -> Mining Language."
    return (
        'Turkish mining needs zeyrek. Install with: pip install "anki-miner[tr]" - '
        "or download the Turkish pack in Settings -> Mining Language."
    )

"""Can this machine mine Vietnamese? (the profile's ``unavailable_reason``).

Four hard requirements, the pack's four components (the tokenize/tag path's whole
third-party closure). Each is satisfied by an importable package (a pip install with the
``[vi]`` extra; a pack root appended to ``sys.path`` at boot) or by the pack on disk.
``find_spec`` answers without importing, through the spaCy languages' own probe helpers.
"""

from __future__ import annotations

import sys

from anki_miner.languages._spaced.availability import _importable, _pack_component_present

#: A frozen bundle has no pip: the sentence names the download button instead.
VI_FROZEN_REASON = "Vietnamese mining needs the Vietnamese language pack. Download it in Settings -> Mining Language."


def vi_missing_reason() -> str | None:
    """Name the missing pack components, or None when Vietnamese can be mined here."""
    from anki_miner.languages.vi.pack import PACK

    missing = [
        comp.import_name
        for comp in PACK.components
        if comp.required and not (_importable(comp.import_name) or _pack_component_present("vi", comp.import_name))
    ]
    if not missing:
        return None
    if getattr(sys, "frozen", False):
        return VI_FROZEN_REASON
    return (
        f"Vietnamese mining needs {', '.join(missing)}. "
        'Install with: pip install "anki-miner[vi]" - or download the Vietnamese pack in Settings -> Mining Language.'
    )

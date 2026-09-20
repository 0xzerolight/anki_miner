"""Ukrainian SubtitleParser factory: the shared spaced factory with the S24 stressed headword (P3, P7)."""

from __future__ import annotations

from typing import Any

from anki_miner.languages._spaced.keys import folded_reading_lookup
from anki_miner.languages.uk.morphology import UK_KEYS


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser

    lookup = kwargs.get("reading_lookup")
    if lookup is not None:
        # The probe NFC-matches terms stored through UK_KEYS; a typographic-apostrophe front must
        # ask for the U+0027 spelling the importer stored.
        kwargs["reading_lookup"] = folded_reading_lookup(lookup, UK_KEYS.fold_term)
    kwargs.setdefault("attested_reading_fallback", True)
    return create_spaced_parser(config, **kwargs)

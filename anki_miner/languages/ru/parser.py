"""Russian SubtitleParser factory: the shared spaced factory with the S24 stressed headword (plan D1, D2)."""

from __future__ import annotations

from typing import Any

from anki_miner.languages._spaced.keys import folded_reading_lookup
from anki_miner.languages.ru.morphology import RU_KEYS


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages.registry import get_profile

    lookup = kwargs.get("reading_lookup")
    if lookup is not None:
        # The probe NFC-matches terms stored through RU_KEYS; a yo front must ask for the е spelling.
        kwargs["reading_lookup"] = folded_reading_lookup(lookup, RU_KEYS.fold_term)
    kwargs.setdefault("attested_reading_fallback", True)
    # Bilingual cues put an English translation line under the native one, and the
    # flattened cue becomes the card's Sentence (ZH-046, KO-06): the script gate drops it.
    kwargs.setdefault("has_target_script", get_profile(config.language).script.contains_target_script)
    return create_spaced_parser(config, **kwargs)

"""zh SubtitleParser factory (the profile's ``create_parser`` field).

The service is the shared ``SubtitleParserService`` — nothing is subclassed.
Its tokenizer arrives through ``tagger_provider.get_tagger(config.language)``,
wired at Stage 1A. The profile's ``MinedFormPolicy`` IS injected here: without
it ``TokenizedWord.mined_form`` falls back to the JA ``select_mined_form``.
This factory exists so the profile can name a callable without the registry
importing the parser (and, transitively, fugashi) at profile-build time.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Chinese SubtitleParser for ``config``."""
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.subtitle_parser import SubtitleParserService

    profile = get_profile(config.language)
    kwargs.setdefault("mined_form_policy", profile.mined_form)
    kwargs.setdefault("reading_support", profile.reading)
    return SubtitleParserService(config, **kwargs)

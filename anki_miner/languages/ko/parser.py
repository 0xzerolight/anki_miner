"""Korean SubtitleParser factory (the profile's create_parser callable).

The service is the shared SubtitleParserService; its tokenizer arrives through
tagger_provider.get_tagger(config.language), so nothing is injected for it. This
factory exists so the profile can name a callable without the registry importing
the parser (and, transitively, fugashi) at profile-build time.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Korean SubtitleParser.

    Supplies the three seams the shared service leaves open: the script gate
    (without it ``should_include`` ends on ``has_kanji`` and a pure-hangul run
    mines nothing), the profile's mined-form policy (without it a ``VV`` token
    falls through the JA ``select_mined_form`` table and the card front reads
    먹었어요 instead of 먹다) and the profile's reading support (``None`` for ko
    today — passed anyway so a later Korean ``ReadingSupport`` is a one-field
    change). ``get_profile`` is imported inside the function because the
    registry names this module; ``setdefault`` leaves an explicitly injected
    test double in charge.
    """
    from anki_miner.languages.ko.script import KoreanScript
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.subtitle_parser import SubtitleParserService

    profile = get_profile(config.language)
    kwargs.setdefault("script_gate", KoreanScript().contains_target_script)
    kwargs.setdefault("mined_form_policy", profile.mined_form)
    kwargs.setdefault("reading_support", profile.reading)
    return SubtitleParserService(config, **kwargs)

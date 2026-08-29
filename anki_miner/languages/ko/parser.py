"""Korean SubtitleParser factory (the profile's create_parser callable)."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Korean SubtitleParser.

    The service is the shared SubtitleParserService; its tokenizer arrives
    through tagger_provider.get_tagger(config.language), so nothing is injected
    here. Kept as the profile's create_parser callable so the heavy import stays
    lazy (registry contract). Task 3.5 adds the script_gate, mined_form_policy
    and reading_support arguments.
    """
    from anki_miner.services.subtitle_parser import SubtitleParserService

    return SubtitleParserService(config, **kwargs)

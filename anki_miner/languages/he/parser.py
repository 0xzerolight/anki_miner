"""Hebrew SubtitleParser factory (the profile's ``create_parser`` field).

The service is the shared ``SubtitleParserService`` -- nothing is subclassed. The tokenizer arrives
through ``tagger_provider.get_tagger(config.language)``.

Two seams this factory closes that the ja path leaves open: ``compound_matching=False`` (S7 -- the
matcher joins adjacent tokens with "", which in a spaced language prints ``NewYork``), which the
shared spaced factory passes, and the ``token_post_pass``, which is the whole of Hebrew's
morphology. The post-pass reads R36's ``form_lookup``, which ``service_factory`` wires whenever an
indexed dictionary is enabled; with none, it is ``None`` and every front is the folded surface.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Hebrew SubtitleParser for ``config``."""
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages.he.morphology import HebrewLemmaPass

    kwargs.setdefault("token_post_pass", HebrewLemmaPass())
    return create_spaced_parser(config, **kwargs)

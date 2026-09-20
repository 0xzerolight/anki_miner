"""Hebrew SubtitleParser factory (the profile's ``create_parser`` field).

The service is the shared ``SubtitleParserService`` -- nothing is subclassed. The tokenizer arrives
through ``tagger_provider.get_tagger(config.language)``.

Two seams this factory closes that the ja path leaves open: ``compound_matching=False`` (S7 -- the
matcher joins adjacent tokens with "", which in a spaced language prints ``NewYork``), and the
``token_post_pass``, which is the whole of Hebrew's morphology. The post-pass reads R36's
``form_lookup``, which ``service_factory`` wires whenever an indexed dictionary is enabled; with
none, it is ``None`` and every front is the folded surface.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Hebrew SubtitleParser for ``config``."""
    from anki_miner.languages.he.morphology import HebrewLemmaPass
    from anki_miner.languages.registry import bound_mined_form, get_profile
    from anki_miner.services.subtitle_parser import SubtitleParserService

    profile = get_profile(config.language)
    kwargs.setdefault("script_gate", profile.script.contains_target_script)
    kwargs.setdefault("mined_form_policy", bound_mined_form(profile, config))
    kwargs.setdefault("reading_support", profile.reading)
    kwargs.setdefault("normalize", profile.normalize)
    kwargs.setdefault("compound_matching", False)
    kwargs.setdefault("sentence_annotation", profile.sentence_annotator is not None)
    kwargs.setdefault("token_post_pass", HebrewLemmaPass())
    return SubtitleParserService(config, **kwargs)

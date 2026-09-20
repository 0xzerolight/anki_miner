"""th SubtitleParser factory (the profile's ``create_parser`` field).

The service is the shared ``SubtitleParserService`` -- nothing is subclassed.
The tokenizer arrives through ``tagger_provider.get_tagger(config.language)``.

``compound_matching=False`` (S7) is the one seam this factory closes that zh
leaves open: the compound matcher greedily joins adjacent tokens against the
installed dictionary, and with no spaces to stop it a Thai line merges up to
five tokens into one attested-looking string.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Thai SubtitleParser for ``config``."""
    from anki_miner.languages.registry import bound_mined_form, get_profile
    from anki_miner.services.subtitle_parser import SubtitleParserService

    profile = get_profile(config.language)
    kwargs.setdefault("script_gate", profile.script.contains_target_script)
    kwargs.setdefault("mined_form_policy", bound_mined_form(profile, config))
    kwargs.setdefault("reading_support", profile.reading)
    kwargs.setdefault("normalize", profile.normalize)
    kwargs.setdefault("compound_matching", False)
    kwargs.setdefault("sentence_annotation", profile.sentence_annotator is not None)
    return SubtitleParserService(config, **kwargs)

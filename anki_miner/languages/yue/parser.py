"""yue SubtitleParser factory (the profile's ``create_parser`` field).

The service is the shared ``SubtitleParserService`` -- nothing is subclassed.
The tokenizer arrives through ``tagger_provider.get_tagger(config.language)``.

Two seams this factory closes that zh leaves open:

``script_gate`` -- in sentence context the tagger gives Latin and digit runs
ordinary tags (measured: ``Netflix`` NOUN, ``2024`` NOUN, ``8`` NOUN), so POS
cannot exclude them and the Han gate has to. zh does not need this because
jieba tags Latin ``eng``/``x``, outside its allowed set.

``compound_matching=False`` (S7) -- the matcher greedily joins adjacent tokens
against the installed dictionary, and Cantonese has no spaces to stop it. With
CC-CEDICT-Canto carrying 163,280 mostly written-Chinese headwords, leaving it on
turns the top risk of this language (written Chinese under Cantonese dialogue)
into a mining behaviour. th closes it for the same reason; zh keeps it on and
that is zh's call, not this file's.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Cantonese SubtitleParser for ``config``."""
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

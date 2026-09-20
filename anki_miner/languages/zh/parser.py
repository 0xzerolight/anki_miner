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
    from anki_miner.languages.registry import bound_mined_form, get_profile
    from anki_miner.services.subtitle_parser import SubtitleParserService

    profile = get_profile(config.language)
    kwargs.setdefault("mined_form_policy", bound_mined_form(profile, config))
    kwargs.setdefault("reading_support", profile.reading)
    # Bilingual zh+en cues are the norm for Chinese fansubs, and the flattened
    # cue becomes the card's Sentence: without this the English translation is
    # printed alongside every Chinese front.
    kwargs.setdefault("has_target_script", profile.script.contains_target_script)
    # NFC and nothing else. The Japanese pair the default runs rewrites no
    # Chinese character, but it deletes the BMP private-use area and U+FFFD from
    # the stored sentence, which is a mojibake cue the learner should see.
    kwargs.setdefault("normalize", profile.normalize)
    # The matcher stamps its synthetics with a UniDic POS, which no jieba tag can
    # equal, so the inclusion gate rejects every merge it proposes: dead code
    # that still costs a dictionary probe per line.
    kwargs.setdefault("compound_matching", False)
    # A single hanzi is a whole word, and drama subtitles stutter (钱…钱不见了…),
    # so the truncation guard would delete the word the line is about.
    kwargs.setdefault("ellipsis_fragment_guard", False)
    # No sentence annotator: the furigana/reading generators would print the
    # sentence with its spaces deleted (spec 6.1 #2).
    kwargs.setdefault("sentence_annotation", profile.sentence_annotator is not None)
    return SubtitleParserService(config, **kwargs)

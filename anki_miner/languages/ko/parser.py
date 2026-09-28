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

    Supplies the four seams the shared service leaves open: the script gate
    (without it ``should_include`` ends on ``has_kanji`` and a pure-hangul run
    mines nothing), the profile's mined-form policy (without it a ``VV`` token
    falls through the JA ``select_mined_form`` table and the card front reads
    먹었어요 instead of 먹다) and the profile's reading support, ``NO_READING``
    while ko has none (``None`` would run the Japanese reading pass: the Word
    Curator's Reading showed stem fragments such as 도 for 돕다, and 나다 got an
    invented reading 나이다). The fourth is the 하다-predicate merge (``predicate_merge``):
    without it a subtitle's 공부하고 mines the bare noun 공부 and 깨끗한 mines the
    bound root 깨끗, which is not a word. The bilingual-cue line gate and the
    ellipsis guard are closed as the zh factory closes them (comments below).
    ``get_profile`` is imported inside the function because the registry names
    this module; ``setdefault`` leaves an explicitly injected test double in
    charge.
    """
    from anki_miner.languages._spaced.reading import NO_READING
    from anki_miner.languages.ko.predicate_merge import KoreanPredicateMerger
    from anki_miner.languages.ko.script import KoreanScript
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.subtitle_parser import SubtitleParserService

    profile = get_profile(config.language)
    kwargs.setdefault("script_gate", KoreanScript().contains_target_script)
    kwargs.setdefault("mined_form_policy", profile.mined_form)
    kwargs.setdefault("reading_support", profile.reading if profile.reading is not None else NO_READING)
    kwargs.setdefault("token_merger", KoreanPredicateMerger())
    # Dual Korean+English subtitles put the translation on its own line of the
    # cue; without the gate it lands in the card's Sentence.
    kwargs.setdefault("has_target_script", profile.script.contains_target_script)
    # Contracted predicates are one syllable (와 = 오다, 줘 = 주다), so on a
    # stutter line (눈… 눈이 와…) the Japanese truncation guard deletes them.
    kwargs.setdefault("ellipsis_fragment_guard", False)
    # No sentence annotator: the furigana/reading generators would print the
    # sentence with its spaces deleted (spec 6.1 #2).
    kwargs.setdefault("sentence_annotation", profile.sentence_annotator is not None)
    return SubtitleParserService(config, **kwargs)

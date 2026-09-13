"""Shared substrate for space-delimited, cased, inflected mining languages (spec §4.1).

NOT a language: the leading underscore keeps it out of ``AVAILABLE_LANGUAGES``
and ``registry._discover``. Every mechanism here takes its language data as an
argument and branches on no language code; ``languages/<code>/`` supplies the
data. Nothing in this package imports an engine, Qt or ``anki_miner.gui`` at
module import — ``spacy`` is imported function-locally by ``tokenizer.py`` — so
every spaCy profile builds on a machine without spaCy.
"""

from __future__ import annotations

from typing import Any


def create_spaced_parser(config: Any, **kwargs: Any) -> Any:
    """The SubtitleParser factory every spaCy language's ``parser.py`` delegates to.

    Fills the seams the shared service leaves open, each through ``setdefault``
    so an explicitly injected test double stays in charge: the Latin script
    gate, the lemma mined-form policy, the profile's reading support (``None``),
    the profile's ``normalize`` (S5 — the factory is the single source of a
    language's normaliser, Stage S D20), no compound matcher (S7 — a spaced
    match would print ``NewYork``), and no sentence furigana/reading (S13: a
    profile without a ``sentence_annotator``). A language needing a token
    post-pass (de/nl separable verbs) adds
    ``kwargs.setdefault("token_post_pass", SeparableVerbPass())`` first.
    ``get_profile`` is imported here because the registry names the language
    packages that name this function.
    """
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.subtitle_parser import SubtitleParserService

    profile = get_profile(config.language)
    kwargs.setdefault("script_gate", profile.script.contains_target_script)
    kwargs.setdefault("mined_form_policy", profile.mined_form)
    kwargs.setdefault("reading_support", profile.reading)
    kwargs.setdefault("normalize", profile.normalize)
    kwargs.setdefault("compound_matching", False)
    kwargs.setdefault("sentence_annotation", profile.sentence_annotator is not None)
    return SubtitleParserService(config, **kwargs)

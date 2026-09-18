"""Lithuanian language profile: every field constructed from the shared spaCy substrate (spec Appendix E)."""

from __future__ import annotations

import dataclasses

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.availability import spaced_missing_reason
from anki_miner.languages._spaced.fields import (
    NOUN_GENDER_FIELD,
    POS_FIELD,
    spaced_card_fields,
    spaced_scoped_defaults,
)
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.lt.catalog import LT_CATALOG
from anki_miner.languages.lt.morphology import (
    LT_ABBREVIATIONS,
    LT_ALLOWED_POS,
    LT_CLOSERS,
    LT_EXCLUDED_SUBTYPES,
    LT_MODEL_PACKAGE,
    LT_OPENERS,
    LT_SUBTITLE_REGEX,
    lt_normalize,
    strip_stress_marks,
)
from anki_miner.languages.lt.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

LT_SMOKE_SENTENCE = "Knyga yra ant stalo."
LT_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD)
LT_CARD_FIELDS = spaced_card_fields(LT_EXTRA_CARD_FIELDS)
#: D-1: the stress fold runs on both index ends, so a stressed key and a plain query meet.
LT_KEYS = CasefoldDictKeys(extra_fold=strip_stress_marks)

LT_AUDIO = AudioDefaults(
    gtts_lang="lt",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_lt",
    sentence_cache_stem_prefix="sentencetts_lt",
    custom_fetcher_language="lt",
    papago_speaker=None,
    # Wiktionary native audio is Stage W (not built).
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Lithuanian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="lt",
        display_name="Lietuvių",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        # lemma, surface, casefold (E.2.8); no stress-stripped rung — the fold is in the keys.
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"lit", "lt", "lithuanian"}),
        # D19: cp1257 is the Baltic single-byte page; no second leg (cp1252 decodes the same bytes as
        # Western letters without raising, so a third rung could never be reached).
        import_encodings=("utf-8-sig", "cp1257"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="lt",
            audio=LT_AUDIO,
            allowed_pos=LT_ALLOWED_POS,
            excluded_subtypes=LT_EXCLUDED_SUBTYPES,
            card_fields=LT_CARD_FIELDS,
            subtitle_regex=LT_SUBTITLE_REGEX,
        ),
        # Lithuanian opens with „ and closes with “ (ALKSNIS: 498 „, 479 “, 15 ”).
        sentence_rules=dataclasses.replace(sentence_rules(LT_ABBREVIATIONS), openers=LT_OPENERS, closers=LT_CLOSERS),
        normalize=lt_normalize,
        dict_keys=LT_KEYS,
        audio=LT_AUDIO,
        asr_language="lt",
        captions=CaptionLangs(
            primary="lt",
            codes=("lt",),
            orig_codes=("lt-orig",),
            audio_pattern="^lt(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=LT_ALLOWED_POS, excluded_subtypes=LT_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=LT_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"}),
        card_field_defaults=LT_CARD_FIELDS,
        # Default gender labels (masculine/feminine): Lithuanian nouns have no neuter.
        render_hooks=(PosHook(), GrammarTagHook(("noun_gender",))),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("lt", "Lithuanian", LT_MODEL_PACKAGE),
        extra_card_fields=LT_EXTRA_CARD_FIELDS,
        smoke_sentence=LT_SMOKE_SENTENCE,
        english_name="Lithuanian",
        wiktionary_code="",
        # No S3 leading-word table: Lithuanian has no articles.
        dedup_fold=spaced_dedup_fold(LT_KEYS),
    )

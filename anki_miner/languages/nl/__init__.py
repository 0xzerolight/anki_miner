"""Dutch language profile: every field constructed from the shared spaCy substrate."""

from __future__ import annotations

import dataclasses

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.availability import spaced_missing_reason
from anki_miner.languages._spaced.fields import (
    NOUN_ARTICLE_FIELD,
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
from anki_miner.languages.nl.abbreviations import NL_ABBREVIATIONS
from anki_miner.languages.nl.catalog import NL_CATALOG
from anki_miner.languages.nl.morphology import (
    NL_ALLOWED_POS,
    NL_ARTICLE_MAP,
    NL_EXCLUDED_SUBTYPES,
    NL_EXTRA_OPENERS,
    NL_GRAMMAR_SOURCES,
    NL_LEADING_WORDS,
    NL_MODEL_PACKAGE,
    nl_normalize,
)
from anki_miner.languages.nl.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

NL_SMOKE_SENTENCE = "De student las gisteren een interessant boek."
#: The article first: de/het on every noun is the one field every surveyed Dutch deck carries (B.5).
NL_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_ARTICLE_FIELD, NOUN_GENDER_FIELD)
NL_CARD_FIELDS = spaced_card_fields(NL_EXTRA_CARD_FIELDS)
NL_KEYS = CasefoldDictKeys()

_LATIN_RULES = sentence_rules(NL_ABBREVIATIONS)
NL_SENTENCE_RULES = dataclasses.replace(_LATIN_RULES, openers=_LATIN_RULES.openers | NL_EXTRA_OPENERS)

NL_AUDIO = AudioDefaults(
    gtts_lang="nl",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_nl",
    sentence_cache_stem_prefix="sentencetts_nl",
    custom_fetcher_language="nl",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Dutch profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="nl",
        display_name="Nederlands",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"nld", "dut", "nl", "dutch"}),
        # latin-1 after cp1252 could never win: the single-byte validator rejects its C1 controls (en plan D23).
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="nl",
            audio=NL_AUDIO,
            allowed_pos=NL_ALLOWED_POS,
            excluded_subtypes=NL_EXCLUDED_SUBTYPES,
            card_fields=NL_CARD_FIELDS,
        ),
        sentence_rules=NL_SENTENCE_RULES,
        normalize=nl_normalize,
        dict_keys=NL_KEYS,
        audio=NL_AUDIO,
        asr_language="nl",
        captions=CaptionLangs(
            primary="nl",
            codes=("nl",),
            orig_codes=("nl-orig",),
            audio_pattern="^nl(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=NL_ALLOWED_POS, excluded_subtypes=NL_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=NL_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_article", "noun_gender", "lemmatised_frequency"}),
        card_field_defaults=NL_CARD_FIELDS,
        render_hooks=(
            PosHook(),
            GrammarTagHook(("noun_article", "noun_gender"), article_map=NL_ARTICLE_MAP, sources=NL_GRAMMAR_SOURCES),
        ),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("nl", "Dutch", NL_MODEL_PACKAGE),
        extra_card_fields=NL_EXTRA_CARD_FIELDS,
        smoke_sentence=NL_SMOKE_SENTENCE,
        english_name="Dutch",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(NL_KEYS, NL_LEADING_WORDS),
    )

"""Danish language profile: every field constructed from the shared spaCy substrate."""

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
from anki_miner.languages.da.abbreviations import DA_ABBREVIATIONS
from anki_miner.languages.da.catalog import DA_CATALOG
from anki_miner.languages.da.morphology import (
    DA_ALLOWED_POS,
    DA_ARTICLE_MAP,
    DA_CLOSERS,
    DA_EXCLUDED_SUBTYPES,
    DA_GRAMMAR_SOURCES,
    DA_LEADING_WORDS,
    DA_MODEL_PACKAGE,
    DA_OPENERS,
    DA_SUBTITLE_REGEX,
    da_normalize,
)
from anki_miner.languages.da.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

DA_SMOKE_SENTENCE = "Den studerende læste en interessant bog i går."
#: The article first: en/et is the grammar a Danish noun card is learnt with (E.3.2).
DA_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_ARTICLE_FIELD, NOUN_GENDER_FIELD)
DA_CARD_FIELDS = spaced_card_fields(DA_EXTRA_CARD_FIELDS)
DA_KEYS = CasefoldDictKeys()
DA_SENTENCE_RULES = dataclasses.replace(sentence_rules(DA_ABBREVIATIONS), openers=DA_OPENERS, closers=DA_CLOSERS)

DA_AUDIO = AudioDefaults(
    gtts_lang="da",
    # Namespaced stems keyed on the profile code: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_da",
    sentence_cache_stem_prefix="sentencetts_da",
    custom_fetcher_language="da",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Danish profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="da",
        display_name="Dansk",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"dan", "da", "danish"}),
        # latin-1 after cp1252 could never win: the single-byte validator rejects its C1 controls (en plan D23).
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="da",
            audio=DA_AUDIO,
            allowed_pos=DA_ALLOWED_POS,
            excluded_subtypes=DA_EXCLUDED_SUBTYPES,
            card_fields=DA_CARD_FIELDS,
            subtitle_regex=DA_SUBTITLE_REGEX,
        ),
        sentence_rules=DA_SENTENCE_RULES,
        normalize=da_normalize,
        dict_keys=DA_KEYS,
        audio=DA_AUDIO,
        asr_language="da",
        captions=CaptionLangs(
            primary="da",
            codes=("da",),
            orig_codes=("da-orig",),
            audio_pattern="^da(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=DA_ALLOWED_POS, excluded_subtypes=DA_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=DA_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_article", "noun_gender", "lemmatised_frequency"}),
        card_field_defaults=DA_CARD_FIELDS,
        render_hooks=(
            PosHook(),
            GrammarTagHook(("noun_article", "noun_gender"), article_map=DA_ARTICLE_MAP, sources=DA_GRAMMAR_SOURCES),
        ),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("da", "Danish", DA_MODEL_PACKAGE),
        extra_card_fields=DA_EXTRA_CARD_FIELDS,
        smoke_sentence=DA_SMOKE_SENTENCE,
        english_name="Danish",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(DA_KEYS, DA_LEADING_WORDS),
    )

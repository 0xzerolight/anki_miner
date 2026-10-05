"""Romanian language profile: every field constructed from the shared spaCy substrate (spec Appendix E)."""

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
from anki_miner.languages._spaced.pos import UPOS_LABELS, UPOS_RESCUABLE
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults
from anki_miner.languages.ro.catalog import RO_CATALOG
from anki_miner.languages.ro.morphology import (
    RO_ABBREVIATIONS,
    RO_ALLOWED_POS,
    RO_EXCLUDED_SUBTYPES,
    RO_GRAMMAR_SOURCES,
    RO_LEADING_WORDS,
    RO_MODEL_PACKAGE,
    RO_SUBTITLE_REGEX,
    ro_fold_cedilla,
    ro_normalize,
)
from anki_miner.languages.ro.parser import create_parser

__all__ = ["build_profile"]

RO_SMOKE_SENTENCE = "Studentul a citit o carte interesantă ieri."
RO_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD)
RO_CARD_FIELDS = spaced_card_fields(RO_EXTRA_CARD_FIELDS)


class RomanianDictKeys(CasefoldDictKeys):
    """The Latin key fold, whose form lookup also matches the reading column.

    wty-ro-en keys a form row without diacritics and stores the real spelling as its reading
    (``lasa`` / ``lasă`` -> ``lăsa``, ``mananca`` / ``mănâncă`` -> ``mânca``), so an exact-term read of
    ``lasă`` finds nothing. ``storage.term_rows`` then matches the reading as well, as the definition
    lookup does. Its lemma rows carry no reading, so a reading match is only ever a form row.
    """

    term_rows_match_reading = True


#: R35: the cedilla map runs inside the key fold, at import and at query alike.
RO_KEYS = RomanianDictKeys(extra_fold=ro_fold_cedilla)
#: Romanian opens a quotation with „ (its closer ” is already a shared closer, and so is the inner «…» pair).
#: Additive, so a shared opener added later reaches Romanian too (the pl shape).
_LATIN_RULES = sentence_rules(RO_ABBREVIATIONS)
RO_SENTENCE_RULES = dataclasses.replace(_LATIN_RULES, openers=_LATIN_RULES.openers | frozenset("„"))

RO_AUDIO = AudioDefaults(
    gtts_lang="ro",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_ro",
    sentence_cache_stem_prefix="sentencetts_ro",
    custom_fetcher_language="ro",
    papago_speaker=None,
    # Lingua Libre leads once the wiktionary audio kind exists (Stage W); Google TTS until then.
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Romanian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="ro",
        display_name="Română",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        # ron is ISO 639-2/T, rum 639-2/B (E.1).
        audio_track_codes=frozenset({"ron", "rum", "ro", "romanian"}),
        # ISO-8859-16 needs no rung: its Romanian letters decode identically under cp1250 once ro_normalize
        # folds the cedillas (pinned through the parser in the media-fixture suite).
        import_encodings=("utf-8-sig", "cp1250"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="ro",
            audio=RO_AUDIO,
            allowed_pos=RO_ALLOWED_POS,
            excluded_subtypes=RO_EXCLUDED_SUBTYPES,
            card_fields=RO_CARD_FIELDS,
            subtitle_regex=RO_SUBTITLE_REGEX,
        ),
        sentence_rules=RO_SENTENCE_RULES,
        normalize=ro_normalize,
        dict_keys=RO_KEYS,
        audio=RO_AUDIO,
        asr_language="ro",
        captions=CaptionLangs(
            primary="ro",
            codes=("ro",),
            orig_codes=("ro-orig",),
            audio_pattern="^ro(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=RO_ALLOWED_POS,
            excluded_subtypes=RO_EXCLUDED_SUBTYPES,
            labels=UPOS_LABELS,
            rescuable_tags=UPOS_RESCUABLE,
        ),
        catalog=RO_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"}),
        card_field_defaults=RO_CARD_FIELDS,
        render_hooks=(
            PosHook(),
            GrammarTagHook(("noun_gender",), sources=RO_GRAMMAR_SOURCES),
        ),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("ro", "Romanian", RO_MODEL_PACKAGE),
        extra_card_fields=RO_EXTRA_CARD_FIELDS,
        smoke_sentence=RO_SMOKE_SENTENCE,
        english_name="Romanian",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(RO_KEYS, RO_LEADING_WORDS),
    )

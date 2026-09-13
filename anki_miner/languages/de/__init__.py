"""German language profile: every field constructed from the shared spaCy substrate."""

from __future__ import annotations

import dataclasses

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.availability import spaced_missing_reason
from anki_miner.languages._spaced.fields import (
    NOUN_GENDER_FIELD,
    NOUN_PLURAL_FIELD,
    POS_FIELD,
    spaced_card_fields,
    spaced_scoped_defaults,
)
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript, nfc_normalize
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.de.catalog import DE_CATALOG
from anki_miner.languages.de.morphology import (
    DE_ABBREVIATIONS,
    DE_ALLOWED_POS,
    DE_CLOSERS,
    DE_EXCLUDED_SUBTYPES,
    DE_GENDER_LABELS,
    DE_LEADING_WORDS,
    DE_MODEL_PACKAGE,
    DE_OPENERS,
    particle_less_verb,
)
from anki_miner.languages.de.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

DE_SMOKE_SENTENCE = "Er sieht sich den Film an."
DE_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD, NOUN_PLURAL_FIELD)
DE_CARD_FIELDS = spaced_card_fields(DE_EXTRA_CARD_FIELDS)
DE_KEYS = CasefoldDictKeys()

DE_AUDIO = AudioDefaults(
    gtts_lang="de",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_de",
    sentence_cache_stem_prefix="sentencetts_de",
    custom_fetcher_language="de",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the German profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="de",
        display_name="Deutsch",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(extra_rungs=(particle_less_verb,)),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"ger", "deu", "de", "german"}),
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="de",
            audio=DE_AUDIO,
            allowed_pos=DE_ALLOWED_POS,
            excluded_subtypes=DE_EXCLUDED_SUBTYPES,
            card_fields=DE_CARD_FIELDS,
        ),
        # German closes a quote with “, which the shared Latin set opens with.
        sentence_rules=dataclasses.replace(sentence_rules(DE_ABBREVIATIONS), openers=DE_OPENERS, closers=DE_CLOSERS),
        normalize=nfc_normalize,
        dict_keys=DE_KEYS,
        audio=DE_AUDIO,
        asr_language="de",
        captions=CaptionLangs(
            primary="de",
            codes=("de", "de-AT", "de-CH"),
            orig_codes=("de-orig",),
            audio_pattern="^de(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=DE_ALLOWED_POS, excluded_subtypes=DE_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=DE_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "noun_plural", "lemmatised_frequency"}),
        card_field_defaults=DE_CARD_FIELDS,
        render_hooks=(PosHook(), GrammarTagHook(("noun_gender", "noun_plural"), gender_labels=DE_GENDER_LABELS)),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("de", "German", DE_MODEL_PACKAGE),
        extra_card_fields=DE_EXTRA_CARD_FIELDS,
        smoke_sentence=DE_SMOKE_SENTENCE,
        english_name="German",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(DE_KEYS, DE_LEADING_WORDS),
    )

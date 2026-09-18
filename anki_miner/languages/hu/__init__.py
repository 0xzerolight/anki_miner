"""Hungarian language profile: every field constructed from the shared spaCy substrate."""

from __future__ import annotations

import dataclasses

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.availability import spaced_missing_reason
from anki_miner.languages._spaced.fields import POS_FIELD, spaced_card_fields, spaced_scoped_defaults
from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript, nfc_normalize
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.hu.abbreviations import HU_ABBREVIATIONS
from anki_miner.languages.hu.catalog import HU_CATALOG
from anki_miner.languages.hu.morphology import (
    HU_ALLOWED_POS,
    HU_CLOSERS,
    HU_EXCLUDED_SUBTYPES,
    HU_LEADING_WORDS,
    HU_MODEL_PACKAGE,
    HU_OPENERS,
    HU_SUBTITLE_REGEX,
    preverb_less_verb,
)
from anki_miner.languages.hu.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

HU_SMOKE_SENTENCE = "A diák tegnap elolvasott egy érdekes könyvet."
#: Part of speech only (E.3.2): Hungarian has no grammatical gender, and a/az is a free function word.
HU_EXTRA_CARD_FIELDS = (POS_FIELD,)
HU_CARD_FIELDS = spaced_card_fields(HU_EXTRA_CARD_FIELDS)
#: NFC + casefold, never diacritic-stripped: ő and ű are vowels of their own (R35).
HU_KEYS = CasefoldDictKeys()
HU_SENTENCE_RULES = dataclasses.replace(sentence_rules(HU_ABBREVIATIONS), openers=HU_OPENERS, closers=HU_CLOSERS)

HU_AUDIO = AudioDefaults(
    gtts_lang="hu",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_hu",
    sentence_cache_stem_prefix="sentencetts_hu",
    custom_fetcher_language="hu",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Hungarian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="hu",
        display_name="Magyar",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(extra_rungs=(preverb_less_verb,)),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"hun", "hu", "hungarian"}),
        # Legacy Hungarian subtitles are cp1250: ő/ű sit at 0xF5/0xFB, which cp1252 would read as õ/û.
        import_encodings=("utf-8-sig", "cp1250"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="hu",
            audio=HU_AUDIO,
            allowed_pos=HU_ALLOWED_POS,
            excluded_subtypes=HU_EXCLUDED_SUBTYPES,
            card_fields=HU_CARD_FIELDS,
            subtitle_regex=HU_SUBTITLE_REGEX,
        ),
        sentence_rules=HU_SENTENCE_RULES,
        normalize=nfc_normalize,
        dict_keys=HU_KEYS,
        audio=HU_AUDIO,
        asr_language="hu",
        captions=CaptionLangs(
            primary="hu",
            codes=("hu",),
            orig_codes=("hu-orig",),
            audio_pattern="^hu(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=HU_ALLOWED_POS, excluded_subtypes=HU_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=HU_CATALOG,
        capabilities=frozenset({"pos_tag", "lemmatised_frequency"}),
        card_field_defaults=HU_CARD_FIELDS,
        render_hooks=(PosHook(),),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("hu", "Hungarian", HU_MODEL_PACKAGE),
        extra_card_fields=HU_EXTRA_CARD_FIELDS,
        smoke_sentence=HU_SMOKE_SENTENCE,
        english_name="Hungarian",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(HU_KEYS, HU_LEADING_WORDS),
    )

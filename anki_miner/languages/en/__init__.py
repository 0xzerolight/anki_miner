"""English language profile: every field constructed from the shared spaCy substrate."""

from __future__ import annotations

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
from anki_miner.languages.en.catalog import EN_CATALOG
from anki_miner.languages.en.morphology import (
    EN_ABBREVIATIONS,
    EN_ALLOWED_POS,
    EN_EXCLUDED_SUBTYPES,
    EN_LEADING_WORDS,
    EN_MODEL_PACKAGE,
)
from anki_miner.languages.en.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

EN_SMOKE_SENTENCE = "The quick brown fox jumps over the lazy dog."
EN_EXTRA_CARD_FIELDS = (POS_FIELD,)
EN_CARD_FIELDS = spaced_card_fields(EN_EXTRA_CARD_FIELDS)
EN_KEYS = CasefoldDictKeys()

EN_AUDIO = AudioDefaults(
    gtts_lang="en",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_en",
    sentence_cache_stem_prefix="sentencetts_en",
    custom_fetcher_language="en",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the English profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="en",
        display_name="English",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"eng", "en", "english"}),
        # A.1 also lists latin-1: it differs from cp1252 only in 0x80-0x9F, where
        # it yields C1 controls, which the single-byte validator rejects, so a
        # latin-1 rung after cp1252 could never win (en plan D23).
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="en",
            audio=EN_AUDIO,
            allowed_pos=EN_ALLOWED_POS,
            excluded_subtypes=EN_EXCLUDED_SUBTYPES,
            card_fields=EN_CARD_FIELDS,
        ),
        sentence_rules=sentence_rules(EN_ABBREVIATIONS),
        normalize=nfc_normalize,
        dict_keys=EN_KEYS,
        audio=EN_AUDIO,
        asr_language="en",
        captions=CaptionLangs(
            primary="en",
            codes=("en", "en-US", "en-GB", "en-CA", "en-AU"),
            orig_codes=("en-orig",),
            audio_pattern="^en(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=EN_ALLOWED_POS, excluded_subtypes=EN_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=EN_CATALOG,
        capabilities=frozenset({"pos_tag", "lemmatised_frequency"}),
        card_field_defaults=EN_CARD_FIELDS,
        render_hooks=(PosHook(),),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("en", "English", EN_MODEL_PACKAGE),
        extra_card_fields=EN_EXTRA_CARD_FIELDS,
        smoke_sentence=EN_SMOKE_SENTENCE,
        english_name="English",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(EN_KEYS, EN_LEADING_WORDS),
    )

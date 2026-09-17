"""Greek language profile: every field constructed from the shared spaCy substrate (spec Appendix E)."""

from __future__ import annotations

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.availability import spaced_missing_reason
from anki_miner.languages._spaced.fields import (
    ASPECT_PAIR_FIELD,
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
from anki_miner.languages._spaced.script import GreekScript, nfc_normalize
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.el.catalog import EL_CATALOG
from anki_miner.languages.el.morphology import (
    EL_ALLOWED_POS,
    EL_EXCLUDED_SUBTYPES,
    EL_GENDER_LABELS,
    EL_LEADING_WORDS,
    EL_MODEL_PACKAGE,
    EL_SUBTITLE_REGEX,
    el_sentence_rules,
)
from anki_miner.languages.el.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

EL_SMOKE_SENTENCE = "Το βιβλίο είναι στο σπίτι."
EL_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
EL_CARD_FIELDS = spaced_card_fields(EL_EXTRA_CARD_FIELDS)
EL_KEYS = CasefoldDictKeys()

EL_AUDIO = AudioDefaults(
    gtts_lang="el",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_el",
    sentence_cache_stem_prefix="sentencetts_el",
    custom_fetcher_language="el",
    papago_speaker=None,
    # Wiktionary native audio is Stage W (not built); for el it would near-always miss (LL ell = 0 files).
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Greek profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="el",
        display_name="Ελληνικά",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        # lemma, surface, casefold (E.2.8); R34: no accent-stripped rung.
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=GreekScript(),
        audio_track_codes=frozenset({"ell", "gre", "el", "greek"}),
        # D19's alternates are left out: behind cp1253 an iso8859_7 leg is reached only by byte 0xAA,
        # and mac_greek is not a validated single-byte codec (it never raises). An ISO-8859-7 file
        # therefore decodes as cp1253, which differs at Ά (0xB6 reads as ¶) and a few symbols.
        import_encodings=("utf-8-sig", "cp1253"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="el",
            audio=EL_AUDIO,
            allowed_pos=EL_ALLOWED_POS,
            excluded_subtypes=EL_EXCLUDED_SUBTYPES,
            card_fields=EL_CARD_FIELDS,
            subtitle_regex=EL_SUBTITLE_REGEX,
        ),
        sentence_rules=el_sentence_rules(),
        # NFC also folds U+037E to ";" and polytonic oxia vowels to their tonos forms (the dictionary keys).
        normalize=nfc_normalize,
        dict_keys=EL_KEYS,
        audio=EL_AUDIO,
        asr_language="el",
        captions=CaptionLangs(
            primary="el",
            codes=("el",),
            orig_codes=("el-orig",),
            audio_pattern="^el(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=EL_ALLOWED_POS, excluded_subtypes=EL_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=EL_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "aspect_pairs", "lemmatised_frequency"}),
        card_field_defaults=EL_CARD_FIELDS,
        render_hooks=(PosHook(), GrammarTagHook(("noun_gender", "aspect_pair"), gender_labels=EL_GENDER_LABELS)),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("el", "Greek", EL_MODEL_PACKAGE),
        extra_card_fields=EL_EXTRA_CARD_FIELDS,
        smoke_sentence=EL_SMOKE_SENTENCE,
        english_name="Greek",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(EL_KEYS, EL_LEADING_WORDS),
    )

"""Arabic language profile (spec Appendix C.1): the in-tree CAMeL analyzer over the calima-msa-r13 pack."""

from __future__ import annotations

from types import MappingProxyType

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_speakable
from anki_miner.languages._spaced.fields import spaced_card_fields, spaced_scoped_defaults
from anki_miner.languages.ar.availability import ar_missing_reason
from anki_miner.languages.ar.catalog import AR_CATALOG
from anki_miner.languages.ar.morphology import (
    AR_ALLOWED_POS,
    AR_EXCLUDED_SUBTYPES,
    AR_POS_LABELS,
    ArabicLookupStrategy,
    ArabicMinedForm,
    ArabicReadingSupport,
    ar_audio_candidates,
)
from anki_miner.languages.ar.parser import create_parser
from anki_miner.languages.ar.render import AR_EXTRA_CARD_FIELDS, ArabicCardHook
from anki_miner.languages.ar.script import (
    AR_SENTENCE_RULES,
    AR_SUBTITLE_REGEX,
    ArabicDictKeys,
    ArabicScript,
    ar_normalize,
)
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, ContentTextStyle, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

AR_SMOKE_SENTENCE = "\u0630\u0647\u0628 \u0627\u0644\u0637\u0627\u0644\u0628 \u0625\u0644\u0649 \u0627\u0644\u0645\u062f\u0631\u0633\u0629 \u0635\u0628\u0627\u062d\u0627\u064b."
AR_KEYS = ArabicDictKeys()
#: The spaced defaults plus the one Arabic difference: the vocalised lemma is the headword every
#: surveyed deck shows, so ``expression_reading`` ships mapped (spec C.1).
AR_CARD_FIELDS = MappingProxyType({**spaced_card_fields(AR_EXTRA_CARD_FIELDS), "expression_reading": "Reading"})
#: Naskh first for tashkeel legibility (spec C.1); the bundled Noto Naskh face arrives with the RTL addendum.
AR_FONT_FAMILIES: tuple[str, ...] = (
    "Noto Naskh Arabic",
    "SF Arabic",
    "Geeza Pro",
    "Segoe UI",
    "Tahoma",
    "Noto Sans Arabic",
    "Amiri",
    "Scheherazade New",
    "DejaVu Sans",
)

AR_AUDIO = AudioDefaults(
    gtts_lang="ar",
    cache_stem_prefix="googletts_ar",
    sentence_cache_stem_prefix="sentencetts_ar",
    custom_fetcher_language="ar",
    papago_speaker=None,
    # No Pod101 dictionary endpoint exists for Arabic; Wiktionary audio is Stage W (not built).
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=ar_audio_candidates,
    speakable=spaced_speakable,
)


def ar_space_wrap(text: str) -> str:
    """Soft-wrap transform: identity (Arabic is space-delimited)."""
    return text


def build_profile() -> LanguageProfile:
    """Build the Arabic profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="ar",
        display_name="\u0627\u0644\u0639\u0631\u0628\u064a\u0629",
        create_parser=create_parser,
        mined_form=ArabicMinedForm(),
        lookup=ArabicLookupStrategy(),
        reading=ArabicReadingSupport(),
        sentence_annotator=None,
        script=ArabicScript(),
        # ISO 639-2/3 and the English name, plus the dialect tags a dubbed track carries (spec C.1).
        audio_track_codes=frozenset({"ara", "ar", "arb", "arabic", "arz", "apc", "ary", "afb", "acm", "aeb"}),
        # One legacy codepage (S11); iso8859_6 is unvalidated and reachable behind cp1256 almost never.
        import_encodings=("utf-8-sig", "cp1256"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="ar",
            audio=AR_AUDIO,
            allowed_pos=AR_ALLOWED_POS,
            excluded_subtypes=AR_EXCLUDED_SUBTYPES,
            card_fields=AR_CARD_FIELDS,
            subtitle_regex=AR_SUBTITLE_REGEX,
        ),
        sentence_rules=AR_SENTENCE_RULES,
        normalize=ar_normalize,
        dict_keys=AR_KEYS,
        audio=AR_AUDIO,
        asr_language="ar",
        captions=CaptionLangs(
            primary="ar",
            codes=("ar",),
            orig_codes=("ar-orig",),
            audio_pattern="^ar(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=AR_ALLOWED_POS, excluded_subtypes=AR_EXCLUDED_SUBTYPES, labels=AR_POS_LABELS
        ),
        catalog=AR_CATALOG,
        capabilities=frozenset({"word_root", "arabic_grammar", "arabic_clitics", "lemmatised_frequency"}),
        card_field_defaults=AR_CARD_FIELDS,
        render_hooks=(ArabicCardHook(),),
        content_style=ContentTextStyle(font_role="ar", families=AR_FONT_FAMILIES, wrap=ar_space_wrap),
        unavailable_reason=ar_missing_reason,
        extra_card_fields=AR_EXTRA_CARD_FIELDS,
        smoke_sentence=AR_SMOKE_SENTENCE,
        english_name="Arabic",
        wiktionary_code="",
        # One function at both seams: a vocalised Anki front and the mined unvocalised lemma are one word.
        dedup_fold=AR_KEYS.fold_term,
    )

"""Finnish language profile: every field constructed from the shared spaCy substrate (spec Appendix E)."""

from __future__ import annotations

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.availability import spaced_missing_reason
from anki_miner.languages._spaced.fields import POS_FIELD, spaced_card_fields, spaced_scoped_defaults
from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.fi.catalog import FI_CATALOG
from anki_miner.languages.fi.morphology import (
    FI_ABBREVIATIONS,
    FI_ALLOWED_POS,
    FI_EXCLUDED_SUBTYPES,
    FI_MODEL_PACKAGE,
    fi_normalize,
)
from anki_miner.languages.fi.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

FI_SMOKE_SENTENCE = "Opiskelija luki mielenkiintoisen kirjan eilen."
#: Part of speech only: Finnish has no gender or article, and wty-fi-en headwords carry no Grammar head line (E.3.2).
FI_EXTRA_CARD_FIELDS = (POS_FIELD,)
FI_CARD_FIELDS = spaced_card_fields(FI_EXTRA_CARD_FIELDS)
#: NFC + casefold; a, o and the Swedish a-ring are letters of their own and are never stripped (R35).
FI_KEYS = CasefoldDictKeys()

FI_AUDIO = AudioDefaults(
    gtts_lang="fi",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_fi",
    sentence_cache_stem_prefix="sentencetts_fi",
    custom_fetcher_language="fi",
    papago_speaker=None,
    # Wiktionary recordings (Fi-, LL-Q1412 (fin)-) lead once the wiktionary kind exists (Stage W); Google TTS until then.
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Finnish profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="fi",
        display_name="Suomi",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"fin", "fi", "finnish"}),
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="fi",
            audio=FI_AUDIO,
            allowed_pos=FI_ALLOWED_POS,
            excluded_subtypes=FI_EXCLUDED_SUBTYPES,
            card_fields=FI_CARD_FIELDS,
        ),
        sentence_rules=sentence_rules(FI_ABBREVIATIONS),
        normalize=fi_normalize,
        dict_keys=FI_KEYS,
        audio=FI_AUDIO,
        asr_language="fi",
        captions=CaptionLangs(
            primary="fi",
            codes=("fi",),
            orig_codes=("fi-orig",),
            audio_pattern="^fi(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=FI_ALLOWED_POS, excluded_subtypes=FI_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=FI_CATALOG,
        capabilities=frozenset({"pos_tag", "lemmatised_frequency"}),
        card_field_defaults=FI_CARD_FIELDS,
        render_hooks=(PosHook(),),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("fi", "Finnish", FI_MODEL_PACKAGE),
        extra_card_fields=FI_EXTRA_CARD_FIELDS,
        smoke_sentence=FI_SMOKE_SENTENCE,
        english_name="Finnish",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(FI_KEYS),
    )

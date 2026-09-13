"""French language profile: every field constructed from the shared spaCy substrate plus French data."""

from __future__ import annotations

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
from anki_miner.languages.fr.catalog import FR_CATALOG
from anki_miner.languages.fr.morphology import (
    FR_ABBREVIATIONS,
    FR_ALLOWED_POS,
    FR_EXCLUDED_SUBTYPES,
    FR_GENDER_LABELS,
    FR_LEADING_WORDS,
    FR_MODEL_PACKAGE,
    FR_SUBTITLE_REGEX,
    fold_apostrophes,
    fr_normalize,
)
from anki_miner.languages.fr.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

FR_SMOKE_SENTENCE = "Le chat dort sur la chaise."
FR_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD)
FR_CARD_FIELDS = spaced_card_fields(FR_EXTRA_CARD_FIELDS)
#: Curly apostrophes fold at both index ends, so ``aujourd’hui`` meets wty's ``aujourd'hui``.
FR_KEYS = CasefoldDictKeys(extra_fold=fold_apostrophes)

FR_AUDIO = AudioDefaults(
    gtts_lang="fr",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_fr",
    sentence_cache_stem_prefix="sentencetts_fr",
    custom_fetcher_language="fr",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the French profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="fr",
        display_name="Français",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"fre", "fra", "fr", "french"}),
        # A.1's trailing latin-1 is omitted (en D23): latin-1 differs from cp1252 only in 0x80-0x9F, where it
        # yields C1 controls, which the S11 single-byte validator rejects, so it could never win after cp1252.
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="fr",
            audio=FR_AUDIO,
            allowed_pos=FR_ALLOWED_POS,
            excluded_subtypes=FR_EXCLUDED_SUBTYPES,
            card_fields=FR_CARD_FIELDS,
            subtitle_regex=FR_SUBTITLE_REGEX,
        ),
        sentence_rules=sentence_rules(FR_ABBREVIATIONS),
        normalize=fr_normalize,
        dict_keys=FR_KEYS,
        audio=FR_AUDIO,
        asr_language="fr",
        captions=CaptionLangs(
            primary="fr",
            codes=("fr", "fr-CA", "fr-FR"),
            orig_codes=("fr-orig",),
            audio_pattern="^fr(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=FR_ALLOWED_POS, excluded_subtypes=FR_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=FR_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"}),
        card_field_defaults=FR_CARD_FIELDS,
        render_hooks=(PosHook(), GrammarTagHook(("noun_gender",), gender_labels=FR_GENDER_LABELS)),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("fr", "French", FR_MODEL_PACKAGE),
        extra_card_fields=FR_EXTRA_CARD_FIELDS,
        smoke_sentence=FR_SMOKE_SENTENCE,
        english_name="French",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(FR_KEYS, FR_LEADING_WORDS),
    )

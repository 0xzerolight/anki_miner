"""Spanish language profile: every field constructed from the shared spaCy substrate."""

from __future__ import annotations

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.availability import spaced_missing_reason
from anki_miner.languages._spaced.fields import NOUN_GENDER_FIELD, POS_FIELD, spaced_card_fields, spaced_scoped_defaults
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript, nfc_normalize
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.es.catalog import ES_CATALOG
from anki_miner.languages.es.morphology import (
    ES_ABBREVIATIONS,
    ES_ALLOWED_POS,
    ES_EXCLUDED_SUBTYPES,
    ES_GENDER_LABELS,
    ES_LEADING_WORDS,
    ES_MODEL_PACKAGE,
)
from anki_miner.languages.es.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

ES_SMOKE_SENTENCE = "El perro corre por el parque."
ES_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD)
ES_CARD_FIELDS = spaced_card_fields(ES_EXTRA_CARD_FIELDS)
ES_KEYS = CasefoldDictKeys()

ES_AUDIO = AudioDefaults(
    gtts_lang="es",  # Castilian; a regional voice knob is deferred (spec §9)
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_es",
    sentence_cache_stem_prefix="sentencetts_es",
    custom_fetcher_language="es",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Spanish profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="es",
        display_name="Español",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        # No enclitic rung (es plan D2): the tokenizer's verb repair already fronts the
        # infinitive, and the surface's own form-of row answers a miss; a stripped
        # stem mostly names the wrong word.
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"spa", "es", "spanish", "esp"}),
        import_encodings=("utf-8-sig", "cp1252"),  # no latin-1 rung: it can never win after cp1252 (en D23)
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="es",
            audio=ES_AUDIO,
            allowed_pos=ES_ALLOWED_POS,
            excluded_subtypes=ES_EXCLUDED_SUBTYPES,
            card_fields=ES_CARD_FIELDS,
        ),
        sentence_rules=sentence_rules(ES_ABBREVIATIONS),
        normalize=nfc_normalize,
        dict_keys=ES_KEYS,
        audio=ES_AUDIO,
        asr_language="es",
        captions=CaptionLangs(
            primary="es",
            codes=("es", "es-419", "es-ES", "es-US"),
            orig_codes=("es-orig",),
            audio_pattern="^es(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=ES_ALLOWED_POS, excluded_subtypes=ES_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=ES_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"}),
        card_field_defaults=ES_CARD_FIELDS,
        render_hooks=(
            PosHook(),
            GrammarTagHook(("noun_gender",), gender_labels=ES_GENDER_LABELS),
        ),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("es", "Spanish", ES_MODEL_PACKAGE),
        extra_card_fields=ES_EXTRA_CARD_FIELDS,
        smoke_sentence=ES_SMOKE_SENTENCE,
        english_name="Spanish",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(ES_KEYS, ES_LEADING_WORDS),
    )

"""Italian language profile: every field constructed from the shared spaCy substrate."""

from __future__ import annotations

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
from anki_miner.languages._spaced.morphology import EncliticRung, LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript, nfc_normalize
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.it.catalog import IT_CATALOG
from anki_miner.languages.it.morphology import (
    IT_ABBREVIATIONS,
    IT_ALLOWED_POS,
    IT_ENCLITIC_CLUSTERS,
    IT_EXCLUDED_SUBTYPES,
    IT_LEADING_WORDS,
    IT_MODEL_PACKAGE,
    it_relemmatize,
    italian_article,
)
from anki_miner.languages.it.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

IT_SMOKE_SENTENCE = "Il gatto dorme sulla sedia."
IT_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD, NOUN_ARTICLE_FIELD)
IT_CARD_FIELDS = spaced_card_fields(IT_EXTRA_CARD_FIELDS)
IT_KEYS = CasefoldDictKeys()

IT_AUDIO = AudioDefaults(
    gtts_lang="it",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_it",
    sentence_cache_stem_prefix="sentencetts_it",
    custom_fetcher_language="it",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Italian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="it",
        display_name="Italiano",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(extra_rungs=(EncliticRung(IT_ENCLITIC_CLUSTERS, relemmatize=it_relemmatize),)),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"ita", "it", "italian"}),
        # A.1's trailing latin-1 is omitted: it differs from cp1252 only in 0x80-0x9F (C1 controls), which the
        # single-byte validation rejects, so it could never win after cp1252 (en plan D23).
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="it",
            audio=IT_AUDIO,
            allowed_pos=IT_ALLOWED_POS,
            excluded_subtypes=IT_EXCLUDED_SUBTYPES,
            card_fields=IT_CARD_FIELDS,
        ),
        sentence_rules=sentence_rules(IT_ABBREVIATIONS),
        normalize=nfc_normalize,
        dict_keys=IT_KEYS,
        audio=IT_AUDIO,
        asr_language="it",
        captions=CaptionLangs(
            primary="it",
            codes=("it",),
            orig_codes=("it-orig",),
            audio_pattern="^it(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=IT_ALLOWED_POS, excluded_subtypes=IT_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=IT_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "noun_article", "lemmatised_frequency"}),
        card_field_defaults=IT_CARD_FIELDS,
        render_hooks=(PosHook(), GrammarTagHook(("noun_gender", "noun_article"), article_rule=italian_article)),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("it", "Italian", IT_MODEL_PACKAGE),
        extra_card_fields=IT_EXTRA_CARD_FIELDS,
        smoke_sentence=IT_SMOKE_SENTENCE,
        english_name="Italian",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(IT_KEYS, IT_LEADING_WORDS),
    )

"""Catalan language profile: every field constructed from the shared spaCy substrate (spec Appendix E)."""

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
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.ca.catalog import CA_CATALOG
from anki_miner.languages.ca.morphology import (
    CA_ABBREVIATIONS,
    CA_ALLOWED_POS,
    CA_EXCLUDED_SUBTYPES,
    CA_GENDER_LABELS,
    CA_LEADING_WORDS,
    CA_MODEL_PACKAGE,
    ca_normalize,
    interpunct_variants,
)
from anki_miner.languages.ca.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

CA_SMOKE_SENTENCE = "L'estudiant va llegir un llibre interessant ahir."
CA_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD)
CA_CARD_FIELDS = spaced_card_fields(CA_EXTRA_CARD_FIELDS)
CA_KEYS = CasefoldDictKeys()

CA_AUDIO = AudioDefaults(
    gtts_lang="ca",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_ca",
    sentence_cache_stem_prefix="sentencetts_ca",
    custom_fetcher_language="ca",
    papago_speaker=None,
    # Lingua Libre (LL-Q7026 (cat)-) leads once the wiktionary kind exists (Stage W); Google TTS until then.
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Catalan profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="ca",
        display_name="Català",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(extra_rungs=(interpunct_variants,)),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"cat", "ca", "catalan"}),
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="ca",
            audio=CA_AUDIO,
            allowed_pos=CA_ALLOWED_POS,
            excluded_subtypes=CA_EXCLUDED_SUBTYPES,
            card_fields=CA_CARD_FIELDS,
        ),
        sentence_rules=sentence_rules(CA_ABBREVIATIONS),
        normalize=ca_normalize,
        dict_keys=CA_KEYS,
        audio=CA_AUDIO,
        asr_language="ca",
        captions=CaptionLangs(
            primary="ca",
            # ca-ES: documented-inert, not observed on four 3Cat probes (2026-09-12); kept like es-419/fr-CA (E.7).
            codes=("ca", "ca-ES"),
            orig_codes=("ca-orig",),
            audio_pattern="^ca(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=CA_ALLOWED_POS, excluded_subtypes=CA_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=CA_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"}),
        card_field_defaults=CA_CARD_FIELDS,
        render_hooks=(PosHook(), GrammarTagHook(("noun_gender",), gender_labels=CA_GENDER_LABELS)),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("ca", "Catalan", CA_MODEL_PACKAGE),
        extra_card_fields=CA_EXTRA_CARD_FIELDS,
        smoke_sentence=CA_SMOKE_SENTENCE,
        english_name="Catalan",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(CA_KEYS, CA_LEADING_WORDS),
    )

"""Polish language profile: every field constructed from the shared spaCy substrate (spec Appendix B)."""

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
from anki_miner.languages._spaced.grammar_hook import DEFAULT_ASPECT_LABELS, GrammarTagHook
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript, nfc_normalize
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.pl.catalog import PL_CATALOG
from anki_miner.languages.pl.morphology import (
    PL_ALLOWED_POS,
    PL_ANIMACY_LABELS,
    PL_EXCLUDED_SUBTYPES,
    PL_GENDER_LABELS,
    PL_KEYS,
    PL_MODEL_PACKAGE,
    PL_SENTENCE_RULES,
    PL_SUBTITLE_REGEX,
    PL_VERB_POS,
    pl_dedup_fold,
)
from anki_miner.languages.pl.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

PL_SMOKE_SENTENCE = "Student przeczytał wczoraj ciekawą książkę."
PL_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
PL_CARD_FIELDS = spaced_card_fields(PL_EXTRA_CARD_FIELDS)

PL_AUDIO = AudioDefaults(
    gtts_lang="pl",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_pl",
    sentence_cache_stem_prefix="sentencetts_pl",
    custom_fetcher_language="pl",
    papago_speaker=None,
    # Wiktionary/Lingua Libre (Pl-, LL-Q809 (pol)-) leads once the wiktionary kind exists (Stage W);
    # Google TTS until then.
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Polish profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="pl",
        display_name="Polski",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"pol", "pl", "polish"}),
        # cp1250 only: ISO-8859-2 decodes through cp1250 without raising (a documented limit, plan P12).
        import_encodings=("utf-8-sig", "cp1250"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="pl",
            audio=PL_AUDIO,
            allowed_pos=PL_ALLOWED_POS,
            excluded_subtypes=PL_EXCLUDED_SUBTYPES,
            card_fields=PL_CARD_FIELDS,
            # R11: ŁUKASZ:/MAŁGORZATA: labels the shared Latin capital class cannot match (P19).
            subtitle_regex=PL_SUBTITLE_REGEX,
        ),
        sentence_rules=PL_SENTENCE_RULES,
        normalize=nfc_normalize,
        dict_keys=PL_KEYS,
        audio=PL_AUDIO,
        asr_language="pl",
        captions=CaptionLangs(
            primary="pl",
            codes=("pl",),
            orig_codes=("pl-orig",),
            audio_pattern="^pl(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=PL_ALLOWED_POS, excluded_subtypes=PL_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=PL_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "aspect_pairs", "lemmatised_frequency"}),
        card_field_defaults=PL_CARD_FIELDS,
        render_hooks=(
            PosHook(),
            # Addendum A: a masculine noun prints m pers / m anim / m inan, from the head line first.
            # S1: a verb prints its aspect and the partner the dictionary names, verbatim - pl passes NO
            # partner_fold, because the D2 mark fold would write zrobic/ukrasc (ć and ś are Polish
            # letters, not marked variants, so folding them misspells the card).
            GrammarTagHook(
                ("noun_gender", "aspect_pair"),
                gender_labels=PL_GENDER_LABELS,
                animacy_labels=PL_ANIMACY_LABELS,
                aspect_labels=DEFAULT_ASPECT_LABELS,
                verb_pos=PL_VERB_POS,
            ),
        ),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("pl", "Polish", PL_MODEL_PACKAGE),
        extra_card_fields=PL_EXTRA_CARD_FIELDS,
        smoke_sentence=PL_SMOKE_SENTENCE,
        english_name="Polish",
        wiktionary_code="",
        dedup_fold=pl_dedup_fold,
    )

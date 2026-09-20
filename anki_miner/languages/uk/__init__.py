"""Ukrainian language profile: the shared spaCy substrate with the S24 stressed headword (spec Appendix B)."""

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
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook, drop_romanisation
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import CyrillicScript
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults
from anki_miner.languages.uk.catalog import UK_CATALOG
from anki_miner.languages.uk.morphology import (
    UK_ALLOWED_POS,
    UK_DEDUP_FOLD,
    UK_EXCLUDED_SUBTYPES,
    UK_GENDER_LABELS,
    UK_KEYS,
    UK_MODEL_PACKAGE,
    UK_MORPHOLOGY_PACKAGES,
    UK_SENTENCE_RULES,
    UK_SUBTITLE_REGEX,
    StressedHeadwordReading,
    uk_normalize,
)
from anki_miner.languages.uk.parser import create_parser

__all__ = ["build_profile"]

UK_SMOKE_SENTENCE = "Студент учора прочитав цікаву книжку."
UK_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
UK_CARD_FIELDS = spaced_card_fields(UK_EXTRA_CARD_FIELDS)

UK_AUDIO = AudioDefaults(
    gtts_lang="uk",
    # Namespaced stems: the stem doubles as the Anki media filename, and Russian must not collide.
    cache_stem_prefix="googletts_uk",
    sentence_cache_stem_prefix="sentencetts_uk",
    custom_fetcher_language="uk",
    papago_speaker=None,
    # Google TTS only: the Wiktionary/Lingua Libre kind is not built (DECIDED 1).
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Ukrainian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="uk",
        display_name="Українська",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        # Owns the reading fields and answers "": the parser's S24 fallback fills the stressed headword.
        reading=StressedHeadwordReading(),
        sentence_annotator=None,
        script=CyrillicScript(),
        audio_track_codes=frozenset({"ukr", "uk", "ukrainian"}),
        # cp1251 only: a KOI8-U file decodes through cp1251 without raising (a documented limit, P11).
        import_encodings=("utf-8-sig", "cp1251"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="uk",
            audio=UK_AUDIO,
            allowed_pos=UK_ALLOWED_POS,
            excluded_subtypes=UK_EXCLUDED_SUBTYPES,
            card_fields=UK_CARD_FIELDS,
            # R11: ІВАН:/ОЛЕНА: labels neither the Latin nor the Russian capital class can match.
            subtitle_regex=UK_SUBTITLE_REGEX,
        ),
        sentence_rules=UK_SENTENCE_RULES,
        normalize=uk_normalize,
        dict_keys=UK_KEYS,
        audio=UK_AUDIO,
        asr_language="uk",
        captions=CaptionLangs(
            primary="uk",
            codes=("uk",),
            orig_codes=("uk-orig",),
            audio_pattern="^uk(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=UK_ALLOWED_POS, excluded_subtypes=UK_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=UK_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "aspect_pairs", "stress_marks", "lemmatised_frequency"}),
        card_field_defaults=UK_CARD_FIELDS,
        render_hooks=(
            PosHook(),
            # wty's head line puts a romanisation between the headword and its grammar words
            # ("<stressed headword> - (cytaty) impf (perfective ...)"): folded off, or the rules
            # read the bracket where they expect the aspect. No partner_fold: the partner is
            # stressed and so is the reading field (plan P9).
            GrammarTagHook(("noun_gender", "aspect_pair"), gender_labels=UK_GENDER_LABELS, head_fold=drop_romanisation),
        ),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason(
            "uk", "Ukrainian", UK_MODEL_PACKAGE, extra_packages=UK_MORPHOLOGY_PACKAGES
        ),
        extra_card_fields=UK_EXTRA_CARD_FIELDS,
        smoke_sentence=UK_SMOKE_SENTENCE,
        english_name="Ukrainian",
        wiktionary_code="",
        dedup_fold=UK_DEDUP_FOLD,
    )

"""Russian language profile: the shared spaCy substrate with the S24 stressed headword (spec Appendix B)."""

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
from anki_miner.languages.ru.catalog import RU_CATALOG
from anki_miner.languages.ru.morphology import (
    RU_ALLOWED_POS,
    RU_DEDUP_FOLD,
    RU_EXCLUDED_SUBTYPES,
    RU_GENDER_LABELS,
    RU_KEYS,
    RU_MODEL_PACKAGE,
    RU_MORPHOLOGY_PACKAGES,
    RU_SENTENCE_RULES,
    RU_SUBTITLE_REGEX,
    StressedHeadwordReading,
    ru_normalize,
)
from anki_miner.languages.ru.parser import create_parser

__all__ = ["build_profile"]

RU_SMOKE_SENTENCE = "Студент вчера прочитал интересную книгу."
RU_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
RU_CARD_FIELDS = spaced_card_fields(RU_EXTRA_CARD_FIELDS)

RU_AUDIO = AudioDefaults(
    gtts_lang="ru",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_ru",
    sentence_cache_stem_prefix="sentencetts_ru",
    custom_fetcher_language="ru",
    papago_speaker=None,
    # Google TTS only: the Wiktionary/Lingua Libre kind is not built (DECIDED 1).
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Russian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="ru",
        display_name="Русский",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        # Owns the reading fields and answers "": the parser's S24 fallback fills the stressed headword.
        reading=StressedHeadwordReading(),
        sentence_annotator=None,
        script=CyrillicScript(),
        audio_track_codes=frozenset({"rus", "ru", "russian"}),
        # cp1251 only: a KOI8-R file decodes through cp1251 without raising (a documented limit, plan D9).
        import_encodings=("utf-8-sig", "cp1251"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="ru",
            audio=RU_AUDIO,
            allowed_pos=RU_ALLOWED_POS,
            excluded_subtypes=RU_EXCLUDED_SUBTYPES,
            card_fields=RU_CARD_FIELDS,
            # R11: ИВАН:/МАША: labels the shared Latin capital class cannot match.
            subtitle_regex=RU_SUBTITLE_REGEX,
        ),
        sentence_rules=RU_SENTENCE_RULES,
        normalize=ru_normalize,
        dict_keys=RU_KEYS,
        audio=RU_AUDIO,
        asr_language="ru",
        captions=CaptionLangs(
            primary="ru",
            codes=("ru",),
            orig_codes=("ru-orig",),
            audio_pattern="^ru(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=RU_ALLOWED_POS, excluded_subtypes=RU_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=RU_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "aspect_pairs", "stress_marks", "lemmatised_frequency"}),
        card_field_defaults=RU_CARD_FIELDS,
        render_hooks=(
            PosHook(),
            # wty's head line puts a romanisation after the headword (читать • (čitátʹ) impf): folded off.
            GrammarTagHook(("noun_gender", "aspect_pair"), gender_labels=RU_GENDER_LABELS, head_fold=drop_romanisation),
        ),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason(
            "ru", "Russian", RU_MODEL_PACKAGE, extra_packages=RU_MORPHOLOGY_PACKAGES
        ),
        extra_card_fields=RU_EXTRA_CARD_FIELDS,
        smoke_sentence=RU_SMOKE_SENTENCE,
        english_name="Russian",
        wiktionary_code="",
        dedup_fold=RU_DEDUP_FOLD,
    )

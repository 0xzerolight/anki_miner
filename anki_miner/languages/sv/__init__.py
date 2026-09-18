"""Swedish language profile: every field constructed from the shared spaCy substrate.

``code`` is ``sv`` everywhere -- wty, spaCy, the dictionary ``sourceLanguage``, the mode probe, cache stems, DB
paths, gTTS, Whisper, YouTube captions and hermitdave's ``content/2018/sv/``. No ``noun_gender`` capability: with
two genders and one article each, a Gender field would print the ``en``/``ett`` the article field already carries.
"""

from __future__ import annotations

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.availability import spaced_missing_reason
from anki_miner.languages._spaced.fields import (
    NOUN_ARTICLE_FIELD,
    NOUN_PLURAL_FIELD,
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
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults
from anki_miner.languages.sv.abbreviations import SV_ABBREVIATIONS
from anki_miner.languages.sv.catalog import SV_CATALOG
from anki_miner.languages.sv.morphology import (
    SV_ALLOWED_POS,
    SV_ARTICLE_MAP,
    SV_EXCLUDED_SUBTYPES,
    SV_LEADING_WORDS,
    SV_MODEL_PACKAGE,
    SV_SUBTITLE_REGEX,
    sv_normalize,
)
from anki_miner.languages.sv.parser import create_parser

__all__ = ["build_profile"]

SV_SMOKE_SENTENCE = "Studenten läste en intressant bok."
SV_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_ARTICLE_FIELD, NOUN_PLURAL_FIELD)
SV_CARD_FIELDS = spaced_card_fields(SV_EXTRA_CARD_FIELDS)
SV_KEYS = CasefoldDictKeys()

SV_AUDIO = AudioDefaults(
    gtts_lang="sv",
    # Namespaced stems keyed on the profile code: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_sv",
    sentence_cache_stem_prefix="sentencetts_sv",
    custom_fetcher_language="sv",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Swedish profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="sv",
        display_name="Svenska",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"swe", "sv", "swedish"}),
        # latin-1 after cp1252 could never win: the single-byte validator rejects its C1 controls (en plan D23).
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="sv",
            audio=SV_AUDIO,
            allowed_pos=SV_ALLOWED_POS,
            excluded_subtypes=SV_EXCLUDED_SUBTYPES,
            card_fields=SV_CARD_FIELDS,
            subtitle_regex=SV_SUBTITLE_REGEX,
        ),
        # Swedish opens and closes with ”, which the shared preset already registers closer-only (E.1).
        sentence_rules=sentence_rules(SV_ABBREVIATIONS),
        normalize=sv_normalize,
        dict_keys=SV_KEYS,
        audio=SV_AUDIO,
        asr_language="sv",
        captions=CaptionLangs(
            primary="sv",
            codes=("sv",),
            orig_codes=("sv-orig",),
            audio_pattern="^sv(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=SV_ALLOWED_POS, excluded_subtypes=SV_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=SV_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_article", "noun_plural", "lemmatised_frequency"}),
        card_field_defaults=SV_CARD_FIELDS,
        render_hooks=(PosHook(), GrammarTagHook(("noun_article", "noun_plural"), article_map=SV_ARTICLE_MAP)),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("sv", "Swedish", SV_MODEL_PACKAGE),
        extra_card_fields=SV_EXTRA_CARD_FIELDS,
        smoke_sentence=SV_SMOKE_SENTENCE,
        english_name="Swedish",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(SV_KEYS, SV_LEADING_WORDS),
    )

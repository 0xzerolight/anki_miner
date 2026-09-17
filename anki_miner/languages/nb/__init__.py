"""Norwegian Bokmål language profile: every field constructed from the shared spaCy substrate.

``code`` is ``nb`` (wty, spaCy, the dictionary ``sourceLanguage``, mode probe, cache stems, DB paths); every
downstream service id is ``no`` or carries both (R27): Google TTS knows ``no`` only, Whisper ``no``, YouTube
captions ``no``/``nb``, hermitdave ``content/2018/no/``. ``nno``/``nn`` (Nynorsk) are deliberately not accepted.
"""

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
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.nb.abbreviations import NB_ABBREVIATIONS
from anki_miner.languages.nb.catalog import NB_CATALOG
from anki_miner.languages.nb.morphology import (
    NB_ALLOWED_POS,
    NB_ARTICLE_MAP,
    NB_EXCLUDED_SUBTYPES,
    NB_LEADING_WORDS,
    NB_MODEL_PACKAGE,
    NB_SUBTITLE_REGEX,
    nb_normalize,
)
from anki_miner.languages.nb.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

NB_SMOKE_SENTENCE = "Studenten leste en interessant bok i går."
NB_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD, NOUN_ARTICLE_FIELD)
NB_CARD_FIELDS = spaced_card_fields(NB_EXTRA_CARD_FIELDS)
NB_KEYS = CasefoldDictKeys()

NB_AUDIO = AudioDefaults(
    # gTTS lists "no" (Norwegian) and no "nb"; the fetcher skips gTTS's own check, so the code must be right (R27).
    gtts_lang="no",
    # Namespaced stems keyed on the profile code: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_nb",
    sentence_cache_stem_prefix="sentencetts_nb",
    custom_fetcher_language="nb",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Norwegian Bokmål profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="nb",
        display_name="Norsk bokmål",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"nor", "nob", "no", "nb", "norwegian"}),
        # latin-1 after cp1252 could never win: the single-byte validator rejects its C1 controls (en plan D23).
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="no,nb",
            audio=NB_AUDIO,
            allowed_pos=NB_ALLOWED_POS,
            excluded_subtypes=NB_EXCLUDED_SUBTYPES,
            card_fields=NB_CARD_FIELDS,
            subtitle_regex=NB_SUBTITLE_REGEX,
        ),
        sentence_rules=sentence_rules(NB_ABBREVIATIONS),
        normalize=nb_normalize,
        dict_keys=NB_KEYS,
        audio=NB_AUDIO,
        asr_language="no",
        captions=CaptionLangs(
            primary="no",
            codes=("no", "nb"),
            orig_codes=("no-orig",),
            audio_pattern="^n[ob](-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=NB_ALLOWED_POS, excluded_subtypes=NB_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=NB_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "noun_article", "lemmatised_frequency"}),
        card_field_defaults=NB_CARD_FIELDS,
        render_hooks=(PosHook(), GrammarTagHook(("noun_gender", "noun_article"), article_map=NB_ARTICLE_MAP)),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("nb", "Norwegian", NB_MODEL_PACKAGE),
        extra_card_fields=NB_EXTRA_CARD_FIELDS,
        smoke_sentence=NB_SMOKE_SENTENCE,
        # ASCII by contract (test_language_contract.py); display_name keeps "bokmål".
        english_name="Norwegian",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(NB_KEYS, NB_LEADING_WORDS),
    )

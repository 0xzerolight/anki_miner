"""Slovenian language profile: every field constructed from the shared spaCy substrate (spec Appendix E)."""

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
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript, nfc_normalize
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults
from anki_miner.languages.sl.abbreviations import SL_ABBREVIATIONS
from anki_miner.languages.sl.catalog import SL_CATALOG
from anki_miner.languages.sl.morphology import (
    SL_ALLOWED_POS,
    SL_EXCLUDED_SUBTYPES,
    SL_MODEL_PACKAGE,
    SL_SUBTITLE_REGEX,
    sl_tone_fold,
)
from anki_miner.languages.sl.parser import create_parser

__all__ = ["build_profile"]

SL_SMOKE_SENTENCE = "Študent je včeraj prebral zanimivo knjigo."
#: No noun_plural: Slovenian has three numbers (singular, dual, plural), so a two-way Plural field
#: would print two thirds of a paradigm and hide the dual, which is the number a learner most needs
#: told. The dictionary would rarely fill it anyway - 6.8 % of noun rows carry a Grammar head line.
SL_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
SL_CARD_FIELDS = spaced_card_fields(SL_EXTRA_CARD_FIELDS)
#: NFC + casefold: Slovenian spells c/s/z with a caron the shared normaliser already composes, and
#: no term carries a ligature, so an extra NFKC would only add unrelated compatibility folds.
SL_KEYS = CasefoldDictKeys()

SL_AUDIO = AudioDefaults(
    # gTTS has no Slovenian voice (tts_langs() 2.5.4), so the synthetic leg is Microsoft Edge
    # read-aloud (seam-edgetts, D14). Petra is the female GA voice; sl-SI-RokNeural is the male one,
    # and a user who prefers it can add the row in Settings -> Word Audio.
    gtts_lang="",
    edge_voice="sl-SI-PetraNeural",
    # Namespaced stems: the stem doubles as the Anki media filename. The Google prefixes keep the
    # shared shape even with no Google leg; the Edge fetcher namespaces its own files by voice.
    cache_stem_prefix="googletts_sl",
    sentence_cache_stem_prefix="sentencetts_sl",
    custom_fetcher_language="sl",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="edgetts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Slovenian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="sl",
        display_name="Slovenščina",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        # slv is ISO 639-2; sl and the English name are the ko shape (D20).
        audio_track_codes=frozenset({"slv", "sl", "slovenian"}),
        import_encodings=("utf-8-sig", "cp1250"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="sl",
            audio=SL_AUDIO,
            allowed_pos=SL_ALLOWED_POS,
            excluded_subtypes=SL_EXCLUDED_SUBTYPES,
            card_fields=SL_CARD_FIELDS,
            subtitle_regex=SL_SUBTITLE_REGEX,
        ),
        # Slovenian writes German-style quotes, which need no rule of their own: the shared
        # openers/closers leave both characters depth-neutral (the low opening quote is tracked by
        # neither side, and the closing one is an unmatched opener, which the splitter's pre-scan
        # keeps depth-neutral), and a quoted question still ends its sentence.
        sentence_rules=sentence_rules(SL_ABBREVIATIONS),
        normalize=nfc_normalize,
        dict_keys=SL_KEYS,
        audio=SL_AUDIO,
        asr_language="sl",
        captions=CaptionLangs(
            primary="sl",
            codes=("sl",),
            orig_codes=("sl-orig",),
            audio_pattern="^sl(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=SL_ALLOWED_POS, excluded_subtypes=SL_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=SL_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "aspect_pairs", "lemmatised_frequency"}),
        card_field_defaults=SL_CARD_FIELDS,
        render_hooks=(
            PosHook(),
            # Shared English labels: the learner reading the back is an English speaker, and
            # Slovenian has no articles to build a gender pair from. The partner fold strips the
            # dictionary's accent notation.
            GrammarTagHook(("noun_gender", "aspect_pair"), partner_fold=sl_tone_fold),
        ),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("sl", "Slovenian", SL_MODEL_PACKAGE),
        extra_card_fields=SL_EXTRA_CARD_FIELDS,
        smoke_sentence=SL_SMOKE_SENTENCE,
        english_name="Slovenian",
        # S3 is empty (D18): Slovenian has no articles, so a deck front carries no leading word the
        # lemma lacks.
        dedup_fold=spaced_dedup_fold(SL_KEYS),
    )

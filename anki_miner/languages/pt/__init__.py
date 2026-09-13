"""Portuguese language profile: every field constructed from the shared spaCy substrate.

One language with a variety switch (B.3): ``config.script_variant`` is ``br``
(the first-visit default) or ``pt``. It picks the Google voice
(``pt_gtts_lang``) and the frequency list the setup wizard pre-ticks
(``catalog.py``); captions and ASR are variety-neutral.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
from anki_miner.languages._spaced.script import LatinScript, nfc_normalize
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults
from anki_miner.languages.pt.catalog import PT_CATALOG
from anki_miner.languages.pt.morphology import (
    PT_ABBREVIATIONS,
    PT_ALLOWED_POS,
    PT_EXCLUDED_SUBTYPES,
    PT_GENDER_LABELS,
    PT_LEADING_WORDS,
    PT_MODEL_PACKAGE,
    pt_dedup_fold,
)
from anki_miner.languages.pt.parser import create_parser

if TYPE_CHECKING:  # annotation-only
    from anki_miner.config.config import AnkiMinerConfig

__all__ = ["build_profile", "pt_gtts_lang", "pt_normalize"]

PT_SMOKE_SENTENCE = "O estudante leu um livro interessante ontem."
PT_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD)
PT_CARD_FIELDS = spaced_card_fields(PT_EXTRA_CARD_FIELDS)
PT_KEYS = CasefoldDictKeys()


def pt_normalize(text: str) -> str:
    """S5 for Portuguese: a no-break space is a space, a soft hyphen is dropped, then NFC.

    Each step changes the model's output: an NFD ``é`` tags DET where NFC tags
    AUX ``ser``, and a soft hyphen stays inside its token (``compu\\u00adtador``),
    where no dictionary row can meet it.
    """
    return nfc_normalize(text.replace("\u00a0", " ").replace("\u00ad", ""))


def pt_gtts_lang(config: AnkiMinerConfig) -> str:
    """Google's European Portuguese voice for the ``pt`` variety, the Brazilian one otherwise (S27)."""
    return "pt-PT" if config.script_variant == "pt" else "pt"


PT_AUDIO = AudioDefaults(
    gtts_lang=pt_gtts_lang,
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_pt",
    sentence_cache_stem_prefix="sentencetts_pt",
    custom_fetcher_language="pt",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def _scoped_defaults() -> dict[str, object]:
    """The spaced first-visit defaults, starting on Brazilian Portuguese (B.3)."""
    defaults = spaced_scoped_defaults(
        subtitle_langs="pt,pt-BR,pt-PT",
        audio=PT_AUDIO,
        allowed_pos=PT_ALLOWED_POS,
        excluded_subtypes=PT_EXCLUDED_SUBTYPES,
        card_fields=PT_CARD_FIELDS,
    )
    defaults["script_variant"] = "br"
    return defaults


def build_profile() -> LanguageProfile:
    """Build the Portuguese profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="pt",
        display_name="Português",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        # pt-BR / pt-PT tags match through their primary subtag (matches_language_tag).
        audio_track_codes=frozenset({"por", "pt", "portuguese"}),
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=_scoped_defaults(),
        sentence_rules=sentence_rules(PT_ABBREVIATIONS),
        normalize=pt_normalize,
        dict_keys=PT_KEYS,
        audio=PT_AUDIO,
        asr_language="pt",
        captions=CaptionLangs(
            primary="pt",
            codes=("pt", "pt-BR", "pt-PT"),
            orig_codes=("pt-orig",),
            audio_pattern="^pt(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=PT_ALLOWED_POS, excluded_subtypes=PT_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=PT_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "regional_variants", "lemmatised_frequency"}),
        card_field_defaults=PT_CARD_FIELDS,
        render_hooks=(PosHook(), GrammarTagHook(("noun_gender",), gender_labels=PT_GENDER_LABELS)),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("pt", "Portuguese", PT_MODEL_PACKAGE),
        extra_card_fields=PT_EXTRA_CARD_FIELDS,
        smoke_sentence=PT_SMOKE_SENTENCE,
        english_name="Portuguese",
        wiktionary_code="",
        dedup_fold=pt_dedup_fold(spaced_dedup_fold(PT_KEYS, PT_LEADING_WORDS)),
    )

"""Turkish language profile: zeyrek behind the deterministic analyzer, on the shared ``_spaced`` substrate (§4.4)."""

from __future__ import annotations

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.fields import POS_FIELD, spaced_card_fields, spaced_scoped_defaults
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults
from anki_miner.languages.tr.abbreviations import TR_ABBREVIATIONS
from anki_miner.languages.tr.availability import tr_missing_reason
from anki_miner.languages.tr.catalog import TR_CATALOG
from anki_miner.languages.tr.morphology import (
    TR_ALLOWED_POS,
    TR_DEDUP_FOLD,
    TR_EXCLUDED_SUBTYPES,
    TR_KEYS,
    TR_SUBTITLE_REGEX,
    tr_normalize,
)
from anki_miner.languages.tr.parser import create_parser

__all__ = ["build_profile"]

TR_SMOKE_SENTENCE = "Öğrenci dün ilginç bir kitap okudu."
#: Part of speech only: Turkish has no grammatical gender and no article.
TR_EXTRA_CARD_FIELDS = (POS_FIELD,)
TR_CARD_FIELDS = spaced_card_fields(TR_EXTRA_CARD_FIELDS)

TR_AUDIO = AudioDefaults(
    gtts_lang="tr",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_tr",
    sentence_cache_stem_prefix="sentencetts_tr",
    custom_fetcher_language="tr",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Turkish profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="tr",
        display_name="Türkçe",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        audio_track_codes=frozenset({"tur", "tr", "turkish"}),
        # Legacy Turkish subtitles are cp1254: ğ ı ş İ Ş Ğ sit where cp1252 has ð ý þ Ý Þ Ð.
        import_encodings=("utf-8-sig", "cp1254"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="tr",
            audio=TR_AUDIO,
            allowed_pos=TR_ALLOWED_POS,
            excluded_subtypes=TR_EXCLUDED_SUBTYPES,
            card_fields=TR_CARD_FIELDS,
            subtitle_regex=TR_SUBTITLE_REGEX,
        ),
        sentence_rules=sentence_rules(TR_ABBREVIATIONS),
        normalize=tr_normalize,
        dict_keys=TR_KEYS,
        audio=TR_AUDIO,
        asr_language="tr",
        captions=CaptionLangs(
            primary="tr",
            codes=("tr",),
            orig_codes=("tr-orig",),
            audio_pattern="^tr(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=TR_ALLOWED_POS, excluded_subtypes=TR_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=TR_CATALOG,
        capabilities=frozenset({"pos_tag", "lemmatised_frequency"}),
        card_field_defaults=TR_CARD_FIELDS,
        render_hooks=(PosHook(),),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=tr_missing_reason,
        extra_card_fields=TR_EXTRA_CARD_FIELDS,
        smoke_sentence=TR_SMOKE_SENTENCE,
        english_name="Turkish",
        wiktionary_code="",
        dedup_fold=TR_DEDUP_FOLD,
    )

"""Indonesian language profile (spec C.5): a first-party tokenizer and ladder, no engine and no pack.

The substrate is ``_spaced``'s (Latin gate, lemma front, NFC normalise, Latin sentence rules,
Google audio, card-field derivation); the Indonesian parts are the regex tokenizer, the stopword
tier, the dictionary-validated deinflection ladder and the Root / Affixes / Formal hooks.
"""

from __future__ import annotations

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.fields import spaced_card_fields, spaced_scoped_defaults
from anki_miner.languages._spaced.keys import spaced_dedup_fold
from anki_miner.languages._spaced.morphology import SpacedMinedForm
from anki_miner.languages._spaced.script import LatinScript, nfc_normalize
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.id.catalog import ID_CATALOG
from anki_miner.languages.id.morphology import (
    ID_ABBREVIATIONS,
    ID_ALLOWED_POS,
    ID_EXCLUDED_SUBTYPES,
    ID_POS_LABELS,
    IndonesianDictKeys,
    IndonesianLookupStrategy,
)
from anki_miner.languages.id.parser import create_parser
from anki_miner.languages.id.render import FormalFormHook, RootAffixHook
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, CardFieldSpec, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

ID_SMOKE_SENTENCE = "Saya sedang membaca buku di rumah."
#: ``root`` carries the capability ar and he share (ruling R-ROOT): the field rows dedup by key.
ROOT_FIELD = CardFieldSpec(key="root", capability="word_root", placeholder="Root")
AFFIXES_FIELD = CardFieldSpec(key="affixes", capability="indonesian_affixes", placeholder="Affixes")
FORMAL_FORM_FIELD = CardFieldSpec(key="formal_form", capability="indonesian_register", placeholder="Formal")
ID_EXTRA_CARD_FIELDS = (ROOT_FIELD, AFFIXES_FIELD, FORMAL_FORM_FIELD)
ID_CARD_FIELDS = spaced_card_fields(ID_EXTRA_CARD_FIELDS)
ID_KEYS = IndonesianDictKeys()
ID_SENTENCE_RULES = sentence_rules(ID_ABBREVIATIONS)

ID_AUDIO = AudioDefaults(
    gtts_lang="id",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_id",
    sentence_cache_stem_prefix="sentencetts_id",
    custom_fetcher_language="id",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Indonesian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="id",
        display_name="Bahasa Indonesia",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=IndonesianLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        # ``in`` is the pre-1989 ISO 639-1 code Java-muxed files and Android locales still write;
        # Malay (ms/may/msa/zsm) is deliberately absent: a Malay dub would mine Malay cards.
        audio_track_codes=frozenset({"ind", "id", "in", "indonesian"}),
        import_encodings=("utf-8-sig", "cp1252"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="id",
            audio=ID_AUDIO,
            allowed_pos=ID_ALLOWED_POS,
            excluded_subtypes=ID_EXCLUDED_SUBTYPES,
            card_fields=ID_CARD_FIELDS,
        ),
        sentence_rules=ID_SENTENCE_RULES,
        normalize=nfc_normalize,
        dict_keys=ID_KEYS,
        audio=ID_AUDIO,
        asr_language="id",
        captions=CaptionLangs(
            primary="id", codes=("id",), orig_codes=("id-orig",), audio_pattern="^id(-|$)", bare_fallback=True
        ),
        pos_defaults=PosDefaults(
            allowed_pos=ID_ALLOWED_POS, excluded_subtypes=ID_EXCLUDED_SUBTYPES, labels=ID_POS_LABELS
        ),
        catalog=ID_CATALOG,
        capabilities=frozenset({"word_root", "indonesian_affixes", "indonesian_register"}),
        card_field_defaults=ID_CARD_FIELDS,
        render_hooks=(RootAffixHook(), FormalFormHook()),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=None,
        extra_card_fields=ID_EXTRA_CARD_FIELDS,
        smoke_sentence=ID_SMOKE_SENTENCE,
        english_name="Indonesian",
        wiktionary_code="",
        dedup_fold=spaced_dedup_fold(ID_KEYS),
    )

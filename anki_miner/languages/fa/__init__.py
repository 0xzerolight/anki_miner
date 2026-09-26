"""Persian language profile."""

from __future__ import annotations

from anki_miner.languages._spaced import create_spaced_parser
from anki_miner.languages._spaced.fields import spaced_card_fields, spaced_scoped_defaults
from anki_miner.languages.fa.audio import FA_AUDIO
from anki_miner.languages.fa.availability import fa_missing_reason
from anki_miner.languages.fa.catalog import FA_CATALOG
from anki_miner.languages.fa.lookup import PersianLookupStrategy
from anki_miner.languages.fa.morphology import (
    FA_ALLOWED_POS,
    FA_EXCLUDED_SUBTYPES,
    FA_POS_LABELS,
    PersianMinedForm,
)
from anki_miner.languages.fa.render import FA_RENDER_HOOKS
from anki_miner.languages.fa.script import (
    FA_SENTENCE_RULES,
    FA_SUBTITLE_REGEX,
    ZWNJ,
    PersianDictKeys,
    PersianScript,
    fa_fold,
    fa_normalize,
)
from anki_miner.languages.fa.style import FA_CONTENT_STYLE
from anki_miner.languages.profile import CaptionLangs, CardFieldSpec, LanguageProfile, PosDefaults

__all__ = ["build_profile", "FA_CARD_FIELDS", "FA_EXTRA_CARD_FIELDS", "FA_SMOKE_SENTENCE"]

#: The bundle smoke line (``ANKI_MINER_SMOKE=fa``): "I go to school every day."
#: The present-tense prefix takes a ZERO WIDTH NON-JOINER, written here as the
#: named constant — this file carries no invisible character of its own.
FA_SMOKE_SENTENCE = f"من هر روز به مدرسه می{ZWNJ}روم."

#: One spec per Persian render hook (render.py). The placeholders are
#: untranslated Anki field-name suggestions, the same convention as
#: FA_CARD_FIELDS' own names.
FA_EXTRA_CARD_FIELDS: tuple[CardFieldSpec, ...] = (
    CardFieldSpec(key="reading_romanized", capability="persian_romanization", placeholder="Romanization"),
    CardFieldSpec(key="colloquial_form", capability="persian_register", placeholder="Colloquial"),
    CardFieldSpec(key="present_stem", capability="persian_stems", placeholder="PresentStem"),
)

#: Persian cards start from the ja default map with both furigana fields
#: unmapped ("" = feature off, the existing empty-name skip). Derived, never
#: hand-written, so a key the config gains reaches Persian too.
#: ``expression_reading`` stays unmapped too: Persian has no respelling, and the
#: dictionary's Latin spelling travels in ``reading_romanized`` instead, which
#: is a render hook and follows the same rule — the mapped field name is the
#: switch, so an unmapped key writes nothing.
FA_CARD_FIELDS: dict[str, str] = dict(spaced_card_fields(FA_EXTRA_CARD_FIELDS))


def build_profile() -> LanguageProfile:
    """Build the Persian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="fa",
        display_name="فارسی",
        # The shared spaced factory fills exactly the seams Persian needs: the
        # script gate, the mined-form policy, the profile's normalise, no
        # compound matcher (the fa tokenizer merges its own light verbs) and no
        # sentence annotation. It branches on no language code, so fa keeps no
        # parser.py of its own.
        create_parser=create_spaced_parser,
        mined_form=PersianMinedForm(),
        lookup=PersianLookupStrategy(),
        # No respelling: the dictionary's romanisation is a card field, not a
        # reading, and it exists for only part of the lexicon.
        reading=None,
        sentence_annotator=None,
        script=PersianScript(),
        # "per" is 639-2/B, which Matroska and ffmpeg write; "fas" is 639-2/T;
        # "pes" and "prs" are the Iranian and Dari variants a dual-audio rip
        # may carry.
        audio_track_codes=frozenset({"per", "fas", "fa", "pes", "prs", "persian", "farsi"}),
        # Legacy Persian subtitles are cp1256. It carries the keheh, the ZWNJ
        # and pe/che/zhe/gaf, but NOT the Farsi yeh - so every such file spells
        # it with the Arabic yeh, which fa_normalize unifies.
        import_encodings=("utf-8-sig", "cp1256"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="fa",
            # gTTS has no Persian voice (tts_langs() lacks "fa"), so the default word
            # audio is the Edge read-aloud leg the seam ships.
            audio=FA_AUDIO,
            allowed_pos=FA_ALLOWED_POS,
            excluded_subtypes=FA_EXCLUDED_SUBTYPES,
            card_fields=FA_CARD_FIELDS,
            subtitle_regex=FA_SUBTITLE_REGEX,
        ),
        sentence_rules=FA_SENTENCE_RULES,
        normalize=fa_normalize,
        dict_keys=PersianDictKeys(),
        audio=FA_AUDIO,
        asr_language="fa",
        captions=CaptionLangs(
            primary="fa",
            codes=("fa",),
            orig_codes=("fa-orig",),
            audio_pattern="^fa(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=FA_ALLOWED_POS,
            excluded_subtypes=FA_EXCLUDED_SUBTYPES,
            labels=FA_POS_LABELS,
        ),
        catalog=FA_CATALOG,
        # "rtl" is shared by fa/ar/he (first to merge adds it to
        # CAPABILITY_VOCABULARY); the other three gate this language's own
        # card-field rows, and "lemmatised_frequency" is R21 — the catalogue's
        # frequency list is imported as surfaces and lemmatised in-app.
        capabilities=frozenset(
            {"persian_romanization", "persian_register", "persian_stems", "rtl", "lemmatised_frequency"}
        ),
        card_field_defaults=FA_CARD_FIELDS,
        render_hooks=FA_RENDER_HOOKS,
        content_style=FA_CONTENT_STYLE,
        # The five hazm tables are a pack, so the selector and the switch have
        # to be able to say a Persian install cannot mine yet.
        unavailable_reason=fa_missing_reason,
        extra_card_fields=FA_EXTRA_CARD_FIELDS,
        smoke_sentence=FA_SMOKE_SENTENCE,
        english_name="Persian",
        # "" means the code itself: the wty edition and the en.wiktionary
        # section are both "fa".
        wiktionary_code="",
        dedup_fold=fa_fold,
    )

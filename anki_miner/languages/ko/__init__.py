"""Korean language profile."""

from __future__ import annotations

from anki_miner.languages.ko import morphology as ko_morphology
from anki_miner.languages.ko.audio import KO_AUDIO
from anki_miner.languages.ko.availability import ko_missing_required_reason
from anki_miner.languages.ko.catalog import KO_CATALOG
from anki_miner.languages.ko.parser import create_parser
from anki_miner.languages.ko.render import KO_RENDER_HOOKS
from anki_miner.languages.ko.script import (
    KO_SENTENCE_RULES,
    KoreanDictKeys,
    KoreanScript,
    ko_normalize,
)
from anki_miner.languages.profile import (
    CaptionLangs,
    ContentTextStyle,
    LanguageProfile,
    PosDefaults,
)

__all__ = ["build_profile"]

#: Korean cards start from the same core fields as Japanese, with every
#: JA-specific field unmapped ("" = feature off, the existing empty-name skip).
#: "hanja" is the ko render hook's own key and follows the same convention: the
#: mapped field name is the switch, so an unmapped key writes nothing.
KO_CARD_FIELDS: dict[str, str] = {
    "word": "Expression",
    "sentence": "Sentence",
    "definition": "MainDefinition",
    "glossary": "",
    "picture": "Picture",
    "audio": "SentenceAudio",
    "expression_furigana": "",
    "expression_reading": "",
    "sentence_furigana": "",
    "sentence_reading": "",
    "pitch_position": "",
    "pitch_category": "",
    "pitch_graph": "",
    "pitch_text": "",
    "frequency": "",
    "frequency_sort": "",
    "source": "",
    "expression_audio": "",
    "hanja": "",
}

#: Face candidates for surfaces showing MINED Korean text (not chrome).
KO_FONT_FAMILIES: tuple[str, ...] = (
    "Noto Sans KR",
    "Malgun Gothic",
    "Apple SD Gothic Neo",
    "NanumGothic",
    "Source Han Sans K",
)


def ko_space_wrap(text: str) -> str:
    """Soft-wrap transform for Korean: identity.

    Korean is space-delimited, so Qt and Anki already have break opportunities.
    The Japanese wrap exists only because Japanese offers none.
    """
    return text


class KoLookupStrategy:
    """LookupStrategy for Korean: the tokenizer lemma, once, with conditions 0.

    The ko parser puts kiwi's lemma in TokenizedWord.orth_base, so orth_base IS
    the lemma fallback. 0 means "pure spelling variant, no deinflection rules
    constraint" - the same value _fallback_candidates emits for orth_base. ctype
    is unused: Korean duck tokens carry no feature.cType.
    """

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        return [(orth_base, 0)] if orth_base and orth_base != word else []


def _scoped_defaults() -> dict[str, object]:
    """First-visit values for EVERY language-scoped field.

    Derived by iterating LANGUAGE_SCOPED_FIELDS (never hand-written, so a new
    scoped field cannot silently miss a Korean default), typed from a blank
    config, then overridden. Nothing is inherited from the JA dataclass defaults:
    a first Korean switch must not arrive with the jmdict chain, the JA name
    wordsets, `ja` subtitle langs or the JA deck name.
    """
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS

    blank = AnkiMinerConfig()
    defaults: dict[str, object] = {}
    for name in LANGUAGE_SCOPED_FIELDS:
        current = getattr(blank, name)
        if isinstance(current, tuple):
            defaults[name] = ()
        elif isinstance(current, bool):
            defaults[name] = False
        elif isinstance(current, str):
            defaults[name] = ""
        else:
            defaults[name] = current
    defaults["downloader_subtitle_langs"] = "ko"
    defaults["expression_audio_chain"] = KO_AUDIO.default_chain
    defaults["allowed_pos"] = ko_morphology.KO_ALLOWED_POS
    defaults["excluded_subtypes"] = ko_morphology.KO_EXCLUDED_SUBTYPES
    defaults["anki_fields"] = dict(KO_CARD_FIELDS)
    # One the blank-by-type loop gets wrong rather than merely empty: "" is not
    # a deck AnkiConnect will accept, and inheriting ja's default would file
    # Korean cards into the Japanese deck. "Anki Miner" is the generic default,
    # not a ja-specific one. The ja note type ("Lapis") IS ja-specific, so ko
    # ships empty and the user picks — same split as zh.
    defaults["anki_deck_name"] = "Anki Miner"
    defaults["script_variant"] = ""
    defaults["reading_tone_color"] = False
    return defaults


def build_profile() -> LanguageProfile:
    """Build the Korean profile."""
    return LanguageProfile(
        code="ko",
        display_name="한국어",
        create_parser=create_parser,
        mined_form=ko_morphology.KoreanMinedForm(),
        lookup=KoLookupStrategy(),
        reading=None,  # spec 3.2: respelling is best-effort; it ships as a render hook
        sentence_annotator=None,
        script=KoreanScript(),
        audio_track_codes=frozenset({"kor", "ko", "korean"}),
        import_encodings=("utf-8-sig", "cp949"),
        scoped_defaults=_scoped_defaults(),
        sentence_rules=KO_SENTENCE_RULES,
        normalize=ko_normalize,
        dict_keys=KoreanDictKeys(),
        audio=KO_AUDIO,
        asr_language="ko",
        captions=CaptionLangs(
            primary="ko",
            codes=("ko",),
            orig_codes=("ko-orig",),
            audio_pattern="^ko(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=ko_morphology.KO_ALLOWED_POS,
            excluded_subtypes=ko_morphology.KO_EXCLUDED_SUBTYPES,
            labels=ko_morphology.KO_POS_LABELS,
        ),
        catalog=KO_CATALOG,  # the KRDICT dict; ko/catalog.py documents the manual frequency import
        capabilities=frozenset({"hangul_filters", "hanja"}),
        card_field_defaults=KO_CARD_FIELDS,
        render_hooks=KO_RENDER_HOOKS,
        content_style=ContentTextStyle(font_role="ko", families=KO_FONT_FAMILIES, wrap=ko_space_wrap),
        # Required packages only, and for Korean both are hard: the profile
        # builds with neither installed, so this probe is the only thing that
        # keeps the selector and the switch off an install that cannot mine.
        unavailable_reason=ko_missing_required_reason,
    )

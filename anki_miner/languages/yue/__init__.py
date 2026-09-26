"""Cantonese language engine (spec F.1).

Every ``pycantonese`` import is function-local (``tokenizer.YueTagger.__init__``,
``reading.word_jyutping``), so importing this package -- and building the yue
LanguageProfile -- never needs the ``anki-miner[yue]`` extra installed.
Availability is reported by ``languages.yue.availability``.

**Contract with zh (R32).** This package imports from zh exactly one symbol,
``zh.render.ZhMeasureWordHook``, which it constructs traditional-first and
neither subclasses nor branches on. That hook pulls ``zh.reading`` and
``zh.variants`` at module level, but both import their engines
function-locally, so no jieba, pypinyin or opencc is loaded.
``zh.tokenizer``, ``zh.audio``, ``zh.catalog``, ``zh.pack``, ``zh.support`` and
``zh.style`` are never imported: the mined-form, ladder and folding shapes are
reimplemented in ``support.py`` and the ``SentenceRules`` literal below is
copied, not shared. Pinned by ``tests/unit/languages/test_yue_profile.py``.
"""

from __future__ import annotations

from collections.abc import Mapping

from anki_miner.languages.profile import (
    CaptionLangs,
    LanguageProfile,
    PosDefaults,
    SentenceRules,
)
from anki_miner.languages.switching import blank_scoped_defaults
from anki_miner.languages.yue.audio import YUE_AUDIO
from anki_miner.languages.yue.availability import yue_missing_required_reason
from anki_miner.languages.yue.catalog import YUE_CATALOG
from anki_miner.languages.yue.fields import YUE_CARD_FIELD_DEFAULTS, YUE_EXTRA_CARD_FIELDS
from anki_miner.languages.yue.normalize import normalize_yue
from anki_miner.languages.yue.parser import create_parser
from anki_miner.languages.yue.pos import YUE_ALLOWED_POS, YUE_EXCLUDED_SUBTYPES, YUE_POS_LABELS
from anki_miner.languages.yue.reading import YueReadingSupport
from anki_miner.languages.yue.render import YUE_RENDER_HOOKS
from anki_miner.languages.yue.style import YUE_CONTENT_STYLE
from anki_miner.languages.yue.support import (
    YueDictKeyFolding,
    YueLookupStrategy,
    YueMinedFormPolicy,
    YueScriptSupport,
)

__all__ = ["build_profile"]

#: The bundle smoke line (spec F.1): one clause with an aspect marker, a
#: classifier and a sentence-final particle, so a broken pack shows up as a
#: missing segmentation rather than a short one.
YUE_SMOKE_SENTENCE = "我今日睇咗一套好好睇嘅戲。"


def _scoped_defaults() -> Mapping[str, object]:
    """Derive a value for EVERY LANGUAGE_SCOPED_FIELDS name, then override."""
    defaults: dict[str, object] = blank_scoped_defaults()
    defaults.update(
        {
            "downloader_subtitle_langs": "yue",
            "expression_audio_chain": YUE_AUDIO.default_chain,
            "allowed_pos": YUE_ALLOWED_POS,
            "excluded_subtypes": YUE_EXCLUDED_SUBTYPES,
            "anki_fields": YUE_CARD_FIELD_DEFAULTS,
            # "" is not a deck AnkiConnect accepts, and inheriting ja's default
            # would file Cantonese cards into the Japanese deck. The ja note type
            # is ja-specific, so yue ships empty and the user picks (zh/th
            # precedent).
            "anki_deck_name": "Anki Miner",
            "anki_note_type": "",
            # The jyutping hook's only consumer; on, like zh's pinyin.
            "reading_tone_color": True,
        }
    )
    return defaults


def build_profile() -> LanguageProfile:
    """Return the Cantonese profile. Called once per process via the registry.

    MUST NOT call ``registry.get_profile``: the registry holds a plain,
    non-reentrant lock across the builder call. That also rules out calling
    ``create_parser`` here -- naming the callable is the point of the field.
    """
    return LanguageProfile(
        code="yue",
        display_name="廣東話",
        create_parser=create_parser,
        mined_form=YueMinedFormPolicy(),
        lookup=YueLookupStrategy(),
        reading=YueReadingSupport(),
        sentence_annotator=None,
        script=YueScriptSupport(),
        # chi, zho, zh and cmn are NOT claimed: zh owns them and on the web they
        # are overwhelmingly Mandarin, so a chi-tagged Cantonese track stays
        # hand-selectable instead of being auto-selected.
        audio_track_codes=frozenset({"yue", "yue-HK", "zh-yue", "cantonese"}),
        # gb18030 BEFORE big5hkscs: big5hkscs decodes simplified gb18030 bytes
        # without raising (PUA share 0.0, measured), so a Big5-first ladder would
        # silently mis-decode a Mandarin file. big5hkscs rather than big5 because
        # it is the only one of the three that encodes colloquial Cantonese --
        # 嘅哋喎喺啲 -> 9d ef 92 5d d8 7b 9d f6 9d f8, while big5 and cp950 both
        # raise UnicodeEncodeError on 嘅.
        import_encodings=("utf-8-sig", "gb18030", "big5hkscs"),
        scoped_defaults=_scoped_defaults(),
        # zh's literal, copied not imported (R32). S8 is inert for a dot-free
        # script, so abbreviations stays empty.
        sentence_rules=SentenceRules(
            terminators=frozenset("。｡！？!?‼⁉⁇⁈"),
            ellipses=frozenset("…‥"),
            openers=frozenset("「｢『（〔［｛〈《【([{｟〝"),
            closers=frozenset("」｣』）〕］｝〉》】)]}｠〟"),
            space_aware=False,
            split_on_whitespace=False,
        ),
        normalize=normalize_yue,
        dict_keys=YueDictKeyFolding(),
        audio=YUE_AUDIO,
        asr_language="yue",
        captions=CaptionLangs(
            primary="yue",
            # zh-Hant is LAST on purpose: under Cantonese audio it is usually
            # written Chinese (書面語), a different register from the dialogue.
            # zh-Hant-HK was observed on none of the four probed videos and is
            # kept as a documented-inert code.
            codes=("yue", "zh-HK", "zh-Hant-HK", "zh-Hant"),
            # Only "yue" identifies Cantonese. The rest are fetch fallbacks, so
            # they stay out of the probe's gates: a Mandarin video subtitled in
            # zh-Hant is written Chinese, not a Cantonese video.
            own_codes=("yue",),
            orig_codes=("yue-orig",),
            audio_pattern="^(yue|zh-HK)(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=YUE_ALLOWED_POS, excluded_subtypes=YUE_EXCLUDED_SUBTYPES, labels=YUE_POS_LABELS
        ),
        catalog=YUE_CATALOG,
        capabilities=frozenset({"jyutping", "tone_color", "measure_word"}),
        card_field_defaults=YUE_CARD_FIELD_DEFAULTS,
        render_hooks=YUE_RENDER_HOOKS,
        content_style=YUE_CONTENT_STYLE,
        unavailable_reason=yue_missing_required_reason,
        extra_card_fields=YUE_EXTRA_CARD_FIELDS,
        smoke_sentence=YUE_SMOKE_SENTENCE,
        english_name="Cantonese",
        # yue is traditional-only in v1, so a word has one spelling and the term
        # key IS the duplicate-card key (zh needs script_key; yue does not).
        dedup_fold=YueDictKeyFolding().dedup_fold,
    )

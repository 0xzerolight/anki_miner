"""Chinese language engine (spec 9.1 / 10.1).

Every third-party zh dependency is imported function-locally so importing this
package — and building the zh LanguageProfile — never needs the ``anki-miner[zh]``
extra installed. Availability is reported by ``languages.zh.availability``.
"""

from __future__ import annotations

from collections.abc import Mapping

from anki_miner.languages.profile import (
    CaptionLangs,
    CardFieldSpec,
    LanguageProfile,
    PosDefaults,
    SentenceRules,
)
from anki_miner.languages.switching import blank_scoped_defaults
from anki_miner.languages.zh.audio import ZH_AUDIO
from anki_miner.languages.zh.availability import zh_missing_required_reason
from anki_miner.languages.zh.catalog import ZH_CATALOG
from anki_miner.languages.zh.fields import ZH_CARD_FIELD_DEFAULTS
from anki_miner.languages.zh.parser import create_parser
from anki_miner.languages.zh.pos import ZH_ALLOWED_POS, ZH_EXCLUDED_SUBTYPES, ZH_POS_LABELS
from anki_miner.languages.zh.reading import ZhReadingSupport
from anki_miner.languages.zh.render import ZH_RENDER_HOOKS
from anki_miner.languages.zh.style import ZH_CONTENT_STYLE
from anki_miner.languages.zh.support import (
    ZhDictKeyFolding,
    ZhLookupStrategy,
    ZhMinedFormPolicy,
    ZhScriptSupport,
)
from anki_miner.languages.zh.variants import normalize_zh

__all__ = ["build_profile"]

#: Copied verbatim from gui/app.py::_LANGUAGE_SMOKE_LINES["zh"].
ZH_SMOKE_SENTENCE = "我今天早上吃了三个苹果。"

#: One spec per ZH_RENDER_HOOKS field (render.py). Placeholders and gating
#: capabilities match the existing hand-written rows in
#: gui/widgets/panels/anki_settings_panel.py (``_language_gate_pairs``)
#: verbatim — "pinyin" gates the expression_pinyin row there, not
#: "tone_color" (which gates the separate reading_tone_color *checkbox* in
#: filtering_settings_panel.py). ``raw_html=True`` matches
#: ``anki_note_builder._RAW_HTML_FIELD_KEYS`` membership exactly.
ZH_EXTRA_CARD_FIELDS: tuple[CardFieldSpec, ...] = (
    CardFieldSpec(key="measure_word", capability="measure_word", placeholder="MeasureWord"),
    CardFieldSpec(key="expression_traditional", capability="script_variants", placeholder="Traditional"),
    CardFieldSpec(key="expression_pinyin", capability="pinyin", placeholder="Pinyin", raw_html=True),
)


def _scoped_defaults() -> Mapping[str, object]:
    """Derive a value for EVERY LANGUAGE_SCOPED_FIELDS name, then override.

    Starts from ``blank_scoped_defaults()`` (derived from the dataclass field
    types, never hand-written: a field added to the tuple can never be
    silently missed — ``switch_language`` raises).
    """
    defaults: dict[str, object] = blank_scoped_defaults()
    defaults.update(
        {
            "downloader_subtitle_langs": "zh-Hans",
            "expression_audio_chain": ZH_AUDIO.default_chain,
            "allowed_pos": ZH_ALLOWED_POS,
            "excluded_subtypes": ZH_EXCLUDED_SUBTYPES,
            "anki_fields": ZH_CARD_FIELD_DEFAULTS,
            # Two the blank-by-type loop gets wrong rather than merely empty.
            # "" is not a deck AnkiConnect will accept, and inheriting ja's
            # default would file Chinese cards into the Japanese deck; "Anki
            # Miner" is the generic default, not a ja-specific one. The ja note
            # type ("Lapis") IS ja-specific — a JP Mining Note layout whose
            # fields a zh run cannot fill — so zh ships empty and the user picks.
            "anki_deck_name": "Anki Miner",
            "anki_note_type": "",
            "script_variant": "simplified",
            "reading_tone_color": True,
        }
    )
    return defaults


def build_profile() -> LanguageProfile:
    """Return the Chinese profile. Called once per process via the registry.

    MUST NOT call ``registry.get_profile``: the registry holds a plain,
    non-reentrant lock across the builder call, so re-entering it deadlocks the
    process with no traceback. That also rules out calling ``create_parser``
    here — the factory resolves the profile through the registry at parse time,
    which is long after this function has returned. Naming the callable is the
    whole point of the field.
    """
    return LanguageProfile(
        code="zh",
        display_name="中文",
        create_parser=create_parser,
        mined_form=ZhMinedFormPolicy(),
        lookup=ZhLookupStrategy(),
        reading=ZhReadingSupport(),
        sentence_annotator=None,
        script=ZhScriptSupport(),
        audio_track_codes=frozenset({"chi", "zho", "zh", "chinese", "cmn"}),
        import_encodings=("utf-8-sig", "gb18030", "big5"),
        scoped_defaults=_scoped_defaults(),
        sentence_rules=SentenceRules(
            terminators=frozenset("。｡！？!?‼⁉⁇⁈"),
            ellipses=frozenset("…‥"),
            openers=frozenset("「｢『（〔［｛〈《【([{｟〝"),
            closers=frozenset("」｣』）〕］｝〉》】)]}｠〟"),
            space_aware=False,
        ),
        normalize=normalize_zh,
        dict_keys=ZhDictKeyFolding(),
        audio=ZH_AUDIO,
        asr_language="zh",
        captions=CaptionLangs(
            primary="zh-Hans",
            codes=("zh-Hans", "zh-Hant", "zh"),
            orig_codes=("zh-Hans-orig", "zh-Hant-orig"),
            audio_pattern="^zh(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=ZH_ALLOWED_POS, excluded_subtypes=ZH_EXCLUDED_SUBTYPES, labels=ZH_POS_LABELS
        ),
        catalog=ZH_CATALOG,
        capabilities=frozenset({"pinyin", "tone_color", "script_variants", "measure_word"}),
        card_field_defaults=ZH_CARD_FIELD_DEFAULTS,
        render_hooks=ZH_RENDER_HOOKS,
        content_style=ZH_CONTENT_STYLE,
        # Required packages only. OpenCC absent leaves the variant lookups empty
        # and mining working, so gating on it would take the language off the
        # selector and refuse the switch over a degraded feature.
        unavailable_reason=zh_missing_required_reason,
        extra_card_fields=ZH_EXTRA_CARD_FIELDS,
        smoke_sentence=ZH_SMOKE_SENTENCE,
        english_name="Chinese",
    )

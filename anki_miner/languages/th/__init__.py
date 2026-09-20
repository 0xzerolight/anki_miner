"""Thai language engine (spec C.3).

Every ``pythainlp`` import is function-local or behind ``_engine``, so importing
this package — and building the th LanguageProfile — never needs the
``anki-miner[th]`` extra installed. Availability is reported by
``languages.th.availability``.
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
from anki_miner.languages.th.audio import TH_AUDIO
from anki_miner.languages.th.availability import th_missing_required_reason
from anki_miner.languages.th.catalog import TH_CATALOG
from anki_miner.languages.th.fields import TH_CARD_FIELD_DEFAULTS
from anki_miner.languages.th.normalize import normalize_th
from anki_miner.languages.th.parser import create_parser
from anki_miner.languages.th.pos import TH_ALLOWED_POS, TH_EXCLUDED_SUBTYPES, TH_POS_LABELS
from anki_miner.languages.th.render import TH_RENDER_HOOKS
from anki_miner.languages.th.style import TH_CONTENT_STYLE
from anki_miner.languages.th.support import (
    ThaiDictKeyFolding,
    ThaiLookupStrategy,
    ThaiMinedFormPolicy,
    ThaiScriptSupport,
)

__all__ = ["build_profile"]

#: The bundle smoke line. Ends with no terminator on purpose: Thai writes few,
#: and the line has to mine with the sentence rules the profile actually ships.
TH_SMOKE_SENTENCE = "วันนี้อากาศดีมาก"

#: Filled in Task 10, beside the two render hooks that emit these keys.
#: ``test_language_contract.test_extra_card_fields_match_the_render_hooks_exactly``
#: asserts spec keys == hook keys, so the spec and its hook land together or the
#: shared test is red (judge-r1 M3).
TH_EXTRA_CARD_FIELDS: tuple[CardFieldSpec, ...] = ()


def _scoped_defaults() -> Mapping[str, object]:
    """Derive a value for EVERY LANGUAGE_SCOPED_FIELDS name, then override."""
    defaults: dict[str, object] = blank_scoped_defaults()
    defaults.update(
        {
            "downloader_subtitle_langs": "th",
            "expression_audio_chain": TH_AUDIO.default_chain,
            "allowed_pos": TH_ALLOWED_POS,
            "excluded_subtypes": TH_EXCLUDED_SUBTYPES,
            "anki_fields": TH_CARD_FIELD_DEFAULTS,
            # "" is not a deck AnkiConnect accepts, and inheriting ja's default
            # would file Thai cards into the Japanese deck. The ja note type is
            # ja-specific, so th ships empty and the user picks (zh precedent).
            "anki_deck_name": "Anki Miner",
            "anki_note_type": "",
        }
    )
    return defaults


def build_profile() -> LanguageProfile:
    """Return the Thai profile. Called once per process via the registry.

    MUST NOT call ``registry.get_profile``: the registry holds a plain,
    non-reentrant lock across the builder call. That also rules out calling
    ``create_parser`` here — naming the callable is the point of the field.
    """
    return LanguageProfile(
        code="th",
        display_name="ไทย",
        create_parser=create_parser,
        mined_form=ThaiMinedFormPolicy(),
        lookup=ThaiLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=ThaiScriptSupport(),
        audio_track_codes=frozenset({"tha", "th", "thai"}),
        # cp874 is a superset of TIS-620 and is what Thai Windows writes.
        # "windows-874" is the WHATWG label, not a Python codec name.
        import_encodings=("utf-8-sig", "cp874"),
        scoped_defaults=_scoped_defaults(),
        sentence_rules=SentenceRules(
            # Thai writes almost no terminator punctuation: the space is the
            # boundary, which is what split_on_whitespace (S9) is for.
            terminators=frozenset("!?"),
            ellipses=frozenset("…"),
            openers=frozenset('“‘("'),
            closers=frozenset('”’)"'),
            space_aware=False,
            # split_on_whitespace=True arrives in Task 9, with the field.
        ),
        normalize=normalize_th,
        dict_keys=ThaiDictKeyFolding(),
        audio=TH_AUDIO,
        asr_language="th",
        captions=CaptionLangs(
            primary="th",
            codes=("th",),
            orig_codes=("th-orig",),
            audio_pattern="^th(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=TH_ALLOWED_POS, excluded_subtypes=TH_EXCLUDED_SUBTYPES, labels=TH_POS_LABELS
        ),
        catalog=TH_CATALOG,
        capabilities=frozenset({"thai_reading", "thai_classifier"}),
        card_field_defaults=TH_CARD_FIELD_DEFAULTS,
        render_hooks=TH_RENDER_HOOKS,
        content_style=TH_CONTENT_STYLE,
        unavailable_reason=th_missing_required_reason,
        extra_card_fields=TH_EXTRA_CARD_FIELDS,
        smoke_sentence=TH_SMOKE_SENTENCE,
        english_name="Thai",
        dedup_fold=ThaiDictKeyFolding().dedup_fold,
    )

"""Extra card-field specs and card-field defaults for spaCy languages (spec §4.7, A.3)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX
from anki_miner.languages.profile import AudioDefaults, CardFieldSpec

POS_FIELD = CardFieldSpec(key="pos", capability="pos_tag", placeholder="PartOfSpeech")
NOUN_GENDER_FIELD = CardFieldSpec(key="noun_gender", capability="noun_gender", placeholder="Gender")
NOUN_ARTICLE_FIELD = CardFieldSpec(key="noun_article", capability="noun_article", placeholder="Article")
NOUN_PLURAL_FIELD = CardFieldSpec(key="noun_plural", capability="noun_plural", placeholder="Plural")


def spaced_card_fields(extra: Sequence[CardFieldSpec]) -> Mapping[str, str]:
    """The ja ``anki_fields`` default with furigana unmapped, plus each extra key empty.

    Derived, never hand-written (the zh ``fields.py`` rule): a key the config
    gains later reaches every spaCy language without an edit here. Furigana is a
    ja-only concept, so both ruby fields map to nothing; every extra key ships
    empty because the mapped field name is the on/off switch.
    """
    from anki_miner.config.config import AnkiMinerConfig  # languages/ stays import-light

    fields = dict(AnkiMinerConfig().anki_fields)
    fields["expression_furigana"] = ""
    fields["sentence_furigana"] = ""
    fields.update({spec.key: "" for spec in extra})
    return MappingProxyType(fields)


def spaced_scoped_defaults(
    *,
    subtitle_langs: str,
    audio: AudioDefaults,
    allowed_pos: tuple[str, ...],
    excluded_subtypes: tuple[str, ...],
    card_fields: Mapping[str, str],
    subtitle_regex: str = LATIN_SUBTITLE_REGEX,
) -> dict[str, object]:
    """First-visit values for EVERY ``LANGUAGE_SCOPED_FIELDS`` name (the ko/zh ``_scoped_defaults`` shape).

    Starts from ``blank_scoped_defaults()`` so a scoped field added later
    cannot be missed. Nothing is inherited from the ja dataclass defaults: the
    jmdict chain, ja subtitle langs and the ja note type are ja-specific; the
    deck name ``Anki Miner`` is the generic default. The SDH filter is ON for a
    first visit (S10) with the Latin default unless the language passes its own
    ``subtitle_regex`` (R11: caption conventions differ, fr ``JEAN : …``); a
    user's own ja filter stays parked in ja's stash. The dict is fresh: a
    caller may override any key (pt ``script_variant``).
    """
    from anki_miner.languages.switching import blank_scoped_defaults

    defaults = blank_scoped_defaults()
    defaults.update(
        {
            "downloader_subtitle_langs": subtitle_langs,
            "expression_audio_chain": audio.default_chain,
            "allowed_pos": allowed_pos,
            "excluded_subtypes": excluded_subtypes,
            "anki_fields": dict(card_fields),
            "anki_deck_name": "Anki Miner",
            "anki_note_type": "",
            "script_variant": "",
            "reading_tone_color": False,
            "use_subtitle_regex_filter": True,
            "subtitle_regex_filter": subtitle_regex,
            "subtitle_regex_replacement": "",
        }
    )
    return defaults

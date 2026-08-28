"""Language-scoped config fields (``switch_language`` lands in task 1A.11)."""

from __future__ import annotations

#: Config fields whose value belongs to the ACTIVE language. Every profile's
#: scoped_defaults is derived by iterating this tuple, never hand-written.
#: Stage 2A task 2A.11 appends "script_variant" and "reading_tone_color".
LANGUAGE_SCOPED_FIELDS: tuple[str, ...] = (
    "dictionary_chain",
    "frequency_chain",
    "pitch_chain",
    "expression_audio_chain",
    "allowed_pos",
    "excluded_subtypes",
    "excluded_wordsets",
    "exclude_hiragana_only_words",
    "exclude_katakana_only_words",
    "known_words_match_kana_variants",
    "anki_fields",
    "anki_deck_name",
    "anki_note_type",
    "card_type",
    "blacklist_path",
    "whitelist_path",
    "use_blacklist",
    "use_whitelist",
    "downloader_subtitle_langs",
    "excluded_decks",
)

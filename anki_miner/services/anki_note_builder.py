"""Field mapping from a CardPayload to an AnkiConnect note dict.

Split out of ``AnkiService.create_cards_batch`` so the per-card field
mapping (glossary routing, media refs, bold-sentence selection, optional
fields) is unit-testable without HTTP mocks. ``AnkiService`` owns batching,
submission, and error recovery; this module owns what goes in each note.
"""

import html
import re
import unicodedata
from dataclasses import dataclass

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import CardPayload
from anki_miner.utils.text_utils import strip_format_chars

# Field keys every config's ``anki_fields`` must contain (AnkiService
# validates this at construction time).
REQUIRED_FIELD_KEYS = {
    "word",
    "sentence",
    "definition",
    "picture",
    "audio",
    "expression_furigana",
    "sentence_furigana",
}

OPTIONAL_FIELD_KEYS = {
    "pitch_position",
    "pitch_category",
    "pitch_graph",
    "pitch_text",
    "frequency",
    "frequency_sort",
    "source",
    "expression_audio",
}


def configured_target_field_names(config: AnkiMinerConfig) -> set[str]:
    """Return non-empty note fields written for the configured card target."""
    field_names = {value for value in config.anki_fields.values() if value}
    if config.card_type:
        marker_field = config.card_type_marker_fields.get(config.card_type, "")
        if marker_field:
            field_names.add(marker_field)
    return field_names


def missing_note_type_message(note_type: str, available: list[str]) -> str:
    """The one sentence every note-type-not-found check raises.

    Shared by ``AnkiService.verify_card_target`` (the mining preflight) and the
    Card Backfill preflight, so a user who trips it from either path reads the
    identical wording. Lives here rather than in ``anki_service`` because the
    backfill caller is deliberately PyQt-free and cannot import that module.
    """
    shown = ", ".join(available[:5])
    more = "..." if len(available) > 5 else ""
    return f"Note type '{note_type}' not found. Available: {shown}{more}. Check Settings → Anki."


def missing_fields_message(note_type: str, missing: set[str], actual: set[str]) -> str:
    """The one sentence every field-absent-from-note-type check raises."""
    shown = ", ".join(sorted(actual)[:5])
    more = "..." if len(actual) > 5 else ""
    return (
        f"Field(s) {', '.join(sorted(missing))} not found on note type "
        f"'{note_type}'. "
        f"Available: {shown}{more}. "
        f"Check Settings → Anki field mapping."
    )


def field_target_collision_message(note_type: str, targets: list[str]) -> str | None:
    """Return the shared error for duplicate nonempty Anki field targets."""
    duplicate_targets = {target for target in targets if target and targets.count(target) > 1}
    if not duplicate_targets:
        return None
    shown = ", ".join(sorted(duplicate_targets))
    return (
        f"Field(s) {shown} mapped more than once. "
        f"Map each Anki Miner field to a different field on note type '{note_type}'."
    )


def field_mapping_error(
    note_type: str,
    ordered_actual: list[str],
    required: set[str],
    word_target: str,
) -> str | None:
    """Return the shared missing/first-field mapping error, if any."""
    actual = set(ordered_actual)
    missing = required - actual
    if missing:
        return missing_fields_message(note_type, missing, actual)
    if not ordered_actual or word_target != ordered_actual[0]:
        first_field = ordered_actual[0] if ordered_actual else "(none)"
        return (
            f"Word field '{word_target}' must map to the first field '{first_field}' "
            f"on note type '{note_type}'. Check Settings → Anki field mapping."
        )
    return None


# Optional fields whose value is pre-rendered HTML/SVG inserted verbatim (like
# glossary), NOT html.escape()d by the OPTIONAL pass — escaping would turn the
# tags into literal text. They follow the skip-when-empty contract: an absent
# value leaves the field untouched rather than blanking it.
_RAW_HTML_FIELD_KEYS = ("frequency", "pitch_graph", "pitch_text")


# Used to normalize a stored first-field value to the same key Anki dedups on.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SOUND_REF_RE = re.compile(r"\[(?:sound|anki:play[^\]]*):[^\]]*\]", re.IGNORECASE)


def _strip_for_dedup(value: str) -> str:
    """Normalize a field value to match Anki's HTML/media-stripped dedup key.

    Anki computes a first-field duplicate checksum after stripping HTML tags and
    media references (its ``strip_html_media``). Our known-words filter compares
    the stored first field against ``mined_form`` — a plain string — so we must
    strip the same way, or a pre-existing card whose Expression carries ``<b>``,
    ``<div>``, ``&entity;`` markup, a ``[sound:...]`` ref, or stray whitespace
    slips the filter and then collides at ``addNotes`` time (the AnkiConnect
    "cannot create note because it is a duplicate" error).

    Mirrors Anki deliberately: it strips HTML/media but NOT ``[reading]``
    furigana brackets, so ``食べる[たべる]`` stays distinct from ``食べる`` here too.

    Goes deliberately STRICTER than Anki in exactly one place: zero-width format
    characters (Cf) are removed. Anki's checksum cannot see them, so a card
    whose Expression is ``\\u202a寮`` — the shape Yomitan/asbplayer mines out of
    Netflix subtitles, which carry U+202A LEFT-TO-RIGHT EMBEDDING — is invisible
    to Anki's own duplicate check AND, before this strip, to the known-words
    filter. Both gates going blind at once is how a second, clean ``寮`` card got
    created. This filter is the only layer that can catch it, so it must.
    Stripping can only make the filter match more, and two strings differing
    only by zero-width characters are the same word on screen.

    Order matters: the strip runs after ``html.unescape`` so an escaped
    ``&#8234;`` is caught too, and before the whitespace collapse so a field
    holding nothing but format characters normalizes to the empty string.
    """
    text = _SOUND_REF_RE.sub("", value)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    text = strip_format_chars(text)
    text = unicodedata.normalize("NFC", text)
    return " ".join(text.split())


@dataclass(frozen=True)
class BuiltNote:
    """One AnkiConnect note dict plus bold-path diagnostics.

    The bold flags feed ``create_cards_batch``'s Issue #20 log line: surface
    whether the precomputed bolded strings actually made it to the note body,
    so users who enable the option but see no bold can tell from the log
    whether the parse populated the fields.
    """

    note: dict
    used_precomputed_bold: bool
    used_bold_fallback: bool


def build_note(item: CardPayload, config: AnkiMinerConfig, stored_files: set[str]) -> BuiltNote:
    """Map one CardPayload to the note dict ``addNotes`` expects.

    Args:
        item: The card payload (word, media, definition, extra fields).
        config: Frozen config providing field mapping, deck, note type, tags.
        stored_files: Filenames confirmed stored in Anki's media collection;
            media fields only reference files in this set so cards never point
            at missing media.

    Returns:
        The note dict plus flags recording whether the bolded-sentence path
        was used or fell back to plain escaping.
    """
    word = item.word
    media = item.media
    definition = item.definition
    extra_fields = item.extra_fields

    # Pull glossary out of extra_fields BEFORE the OPTIONAL pass —
    # OPTIONAL_FIELD_KEYS html.escape()s its values, but glossary
    # is raw HTML and must be sent verbatim.
    glossary_html = ""
    if extra_fields and "glossary" in extra_fields:
        glossary_html = extra_fields["glossary"] or ""
        extra_fields = {k: v for k, v in extra_fields.items() if k != "glossary"}
        if not extra_fields:
            extra_fields = None

    # Pull the raw-HTML optional fields out of extra_fields BEFORE the OPTIONAL
    # pass for the same reason as glossary: they carry pre-rendered markup — a
    # frequency bullet list (<ul><li>Source: rank</li>…</ul>), an inline pitch
    # graph SVG, or a pitch overline span — not escapable text. Escaping would
    # turn the tags into literal text. Sibling scalar fields (frequency_sort,
    # pitch_position) stay in the escaped OPTIONAL pass — escaping a number/digit
    # string is a no-op.
    raw_html_values = dict.fromkeys(_RAW_HTML_FIELD_KEYS, "")
    if extra_fields:
        for raw_key in _RAW_HTML_FIELD_KEYS:
            if raw_key in extra_fields:
                raw_html_values[raw_key] = extra_fields[raw_key] or ""
        extra_fields = {k: v for k, v in extra_fields.items() if k not in _RAW_HTML_FIELD_KEYS} or None

    # Build field values (only reference successfully stored media)
    picture_html = ""
    if media.screenshot_filename and media.screenshot_filename in stored_files:
        picture_html = f'<img src="{html.escape(media.screenshot_filename)}">'

    audio_ref = ""
    if media.audio_filename and media.audio_filename in stored_files:
        audio_ref = f"[sound:{media.audio_filename}]"

    expression_audio_ref = ""
    if media.expression_audio_filename and media.expression_audio_filename in stored_files:
        expression_audio_ref = f"[sound:{media.expression_audio_filename}]"

    # Sentence + SentenceFurigana use the bolded forms when the
    # config flag is on AND the parse pre-computed them. The
    # precomputed forms are already HTML-safe (per-token escape
    # in wrap_target_*); the <b> tags must not be double-escaped.
    # Empty precomputed string means "fall back to escape" — this
    # is the path for entries that came from a code path that
    # did not honor the bold flag (defensive).
    used_precomputed_bold = False
    used_bold_fallback = False
    if config.bold_target_in_sentence and word.sentence_bolded:
        sentence_field = word.sentence_bolded
        used_precomputed_bold = True
    else:
        sentence_field = html.escape(word.sentence)
        if config.bold_target_in_sentence:
            used_bold_fallback = True
    if config.bold_target_in_sentence and word.sentence_furigana_bolded:
        sentence_furigana_field = word.sentence_furigana_bolded
    else:
        sentence_furigana_field = html.escape(word.sentence_furigana)

    # Build fields, skipping any with empty config mapping
    field_data = {
        "word": html.escape(word.mined_form),
        "sentence": sentence_field,
        "definition": definition or "",
        "glossary": glossary_html,
        "frequency": raw_html_values["frequency"],
        "pitch_graph": raw_html_values["pitch_graph"],
        "pitch_text": raw_html_values["pitch_text"],
        "picture": picture_html,
        "audio": audio_ref,
        "expression_audio": expression_audio_ref,
        "expression_furigana": html.escape(word.expression_furigana),
        "expression_reading": html.escape(word.expression_reading),
        "sentence_furigana": sentence_furigana_field,
        "sentence_reading": html.escape(word.sentence_reading),
    }
    fields = {}
    for key, value in field_data.items():
        anki_field_name = config.anki_fields.get(key, "")
        if not anki_field_name:
            continue
        # The raw-HTML fields (frequency, pitch_graph, pitch_text) are inserted
        # verbatim (like glossary). Unlike the always-emitted fields above they
        # follow the optional gating contract: omit entirely when the value is
        # empty so a word with no data leaves the field untouched rather than
        # blanking it.
        if key in _RAW_HTML_FIELD_KEYS and not value:
            continue
        fields[anki_field_name] = value

    # Add optional fields if configured and data available
    if extra_fields:
        for key, value in extra_fields.items():
            anki_field_name = config.anki_fields.get(key, "")
            if key in OPTIONAL_FIELD_KEYS and anki_field_name and value:
                fields[anki_field_name] = html.escape(str(value))

    # JP Mining Note-style card-type marker: stamp a constant "x" into the one
    # marker field matching the active card_type so the note type renders the
    # card as that type. card_type="" (default) writes nothing. Only the active
    # marker is touched; the other three are left for Anki's empty default.
    if config.card_type:
        marker_field = config.card_type_marker_fields.get(config.card_type, "")
        if marker_field:
            fields[marker_field] = "x"

    note: dict = {
        "deckName": config.anki_deck_name,
        "modelName": config.anki_note_type,
        "fields": fields,
        "tags": config.anki_tags.split(),
    }
    # Deck Builder: re-card words that already exist elsewhere in the
    # collection. duplicateScope="deck" keeps cross-episode curation's
    # single-carding meaningful within the new deck. Normal mining emits NO
    # options object, so AnkiConnect applies its implicit default (whole
    # collection, same note type) — byte-identical to the pre-7.3 wire.
    if config.allow_duplicate_cards:
        note["options"] = {"allowDuplicate": True, "duplicateScope": "deck"}

    return BuiltNote(
        note=note,
        used_precomputed_bold=used_precomputed_bold,
        used_bold_fallback=used_bold_fallback,
    )

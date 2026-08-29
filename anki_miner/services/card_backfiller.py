"""Bulk-fill fields on existing miner cards from currently installed resources.

The Card Backfill tool (Utilities → Card Backfill) generalizes the card restyler's
enumerate → chunk → ``notesInfo`` → compute → ``updateNoteFields`` loop
(``card_restyler.restyle_mined_cards``): after the user installs a pitch CSV,
frequency sources, dictionaries, or an audio pack, it proposes values for pitch
graph/text, frequency display/sort, definition, glossary, reading/furigana and
word-audio fields that old cards are missing.

Word audio is the one proposal that is not a pure local lookup: it fetches
through ``config.expression_audio_chain`` during the scan, so the preview keeps
its promise that apply writes exactly what was previewed (a word no enabled
source has never becomes a row), and it is the only field whose value is
rewritten at apply time — Anki media names are content-addressed, so the
``[sound:...]`` ref can only be built once ``storeMediaFile`` confirms a name.

Two phases, both GUI-free and cancellable:

- :func:`scan_backfill` (read-only) computes every proposed value into a
  :class:`BackfillPlan` — the preview table the user approves.
- :func:`apply_backfill` writes the plan's PRECOMPUTED values (what the user
  previewed is exactly what gets written — no recompute), with a per-chunk
  ``notesInfo`` staleness recheck, then tags touched notes ``anki-miner::backfill``.

Field computation mirrors the mining pipeline's canonical recipes in
``EpisodeProcessor`` (see per-field comments); the mined_form-primary +
whole-result lemma-fallback keying for frequency/definitions is load-bearing
(Issues #19/#5 — see ``_phase2_filter``/``_phase4_lookup`` in
``orchestration/episode_processor.py``; editing either recipe means updating
the mirror here).
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from anki_miner.exceptions import SetupError
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.services.anki_note_builder import (
    _HTML_TAG_RE,
    _SOUND_REF_RE,
    _strip_for_dedup,
    field_mapping_error,
    field_target_collision_message,
    missing_note_type_message,
)
from anki_miner.services.backfill_audio import word_audio_candidates

# Generic Anki-search escaper (backslash/quote/``*``/``_``); the historical name
# says "note type" but deck names need the identical escaping (see
# AnkiService._build_vocab_query — ``Core_2k`` would otherwise glob-match).
from anki_miner.services.card_restyler import _escape_note_type as _escape_anki_search
from anki_miner.services.definition_service import collect_dictionary_css_entries
from anki_miner.services.dictionary.card_style_block import attach_card_style_block
from anki_miner.services.frequency.multi_frequency_service import harmonic_rank
from anki_miner.services.frequency.render import render_frequency_html
from anki_miner.services.morphology import extract_lemma
from anki_miner.services.pitch_accent.render import (
    render_pitch_graph_field,
    render_pitch_text_field,
)
from anki_miner.services.subtitle_parser import _differs_by_okurigana_only
from anki_miner.services.tagger import get_shared_tagger
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.text_utils import (
    _format_furigana,
    generate_reading,
    katakana_to_hiragana,
)
from anki_miner.utils.timing import timed_phase

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.anki_service import AnkiService

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

BACKFILL_TAG = "anki-miner::backfill"
_CHUNK = 500

# UI checkbox groups → config.anki_fields keys. The reading group is pure
# cross-fill (one field derived from the other, never generated from a
# tokenizer guess), so its checkbox requires BOTH keys mapped; every other
# group enables when at least one key is mapped.
FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "pitch": ("pitch_graph", "pitch_text"),
    "frequency": ("frequency", "frequency_sort"),
    "definition": ("definition",),
    "glossary": ("glossary",),
    "reading": ("expression_reading", "expression_furigana"),
    "word_audio": ("expression_audio",),
}

_PITCH_KEYS = frozenset(FIELD_GROUPS["pitch"])
_FREQ_KEYS = frozenset(FIELD_GROUPS["frequency"])
# Keys whose stored value is a media reference rather than text. _is_empty
# mirrors Anki's HTML/media-stripped dedup key and therefore strips
# ``[sound:...]``, so for these keys it reports a fully-populated field as
# empty — see _is_fillable, which is the only place that difference matters.
_MEDIA_KEYS = frozenset(FIELD_GROUPS["word_audio"])
# v2.7.8-v2.11.0 wrote this into the sort field for a word no source ranked.
# READ-ONLY now: nothing writes it, and `_is_fillable` treats a stored one as
# empty so a normal fill-only scan can replace it with a real rank.
_LEGACY_FREQ_MISS_SENTINEL = "9999999"
_OLD_DISPLAY_CAP = 200

# One `kanji[reading]` furigana group as _format_furigana renders it: a run
# without brackets/spaces, then its bracketed kana. Used by the inverse scan.
_FURIGANA_GROUP_RE = re.compile(r"([^\[\]\s]+)\[([^\[\]]+)\]")


@dataclass(frozen=True)
class BackfillOptions:
    """User selections for one scan: resolved anki_fields keys + scope."""

    field_keys: frozenset[str]
    deck: str | None = None
    overwrite: bool = False


@dataclass(frozen=True)
class FieldChange:
    """One proposed write: preview row (old_display) + the exact value to write."""

    field_key: str
    field_name: str
    old_display: str
    new_value: str
    #: Local file backing a media field, carried from scan to apply. The final
    #: ``[sound:...]`` value cannot be built at scan time: Anki media names are
    #: content-addressed and only confirmed by ``storeMediaFile``, so
    #: ``new_value`` holds the pre-upload name for the preview row and apply
    #: substitutes the confirmed one. None for every text field.
    media_path: Path | None = None


@dataclass(frozen=True)
class NotePlan:
    note_id: int
    expression: str
    changes: tuple[FieldChange, ...]


@dataclass(frozen=True)
class BackfillPlan:
    """Scan output: everything the preview shows and apply writes."""

    options: BackfillOptions
    notes: tuple[NotePlan, ...]
    scanned: int
    skipped_no_identity: int
    unavailable_fields: tuple[str, ...]
    # Exact mapped field used to capture ``NotePlan.expression``. Apply uses
    # this scan-time name for its compare-before-write identity check.
    expression_field: str
    config_version: int = 0
    # Overwrite-mode fields skipped because the freshly computed value was
    # byte-identical to the stored one. Lets the summary distinguish "already
    # up to date" from "lookups found nothing" on an empty plan.
    identical_skips: int = 0
    # Overwrite-mode pitch fields left alone because the only reading available
    # was a context-free tokenizer guess. Surfaced so an overwrite run that
    # deliberately protects existing pitch does not read as "nothing found".
    guessed_reading_skips: int = 0
    # Selected field NAMES the preflight found are not on the note type — a
    # stale Settings → Anki mapping. Their keys are dropped before the scan,
    # so without this the whole group silently proposes nothing and the summary
    # reads as "already have values".
    absent_fields: tuple[str, ...] = ()

    @property
    def total_field_changes(self) -> int:
        return sum(len(note.changes) for note in self.notes)


@dataclass(frozen=True)
class BackfillResult:
    """Apply outcome with confirmed writes separated from failed writes.

    ``notes_updated``, ``fields_filled``, and ``tagged`` count only note IDs
    confirmed by AnkiConnect. ``failed`` counts attempted note updates that
    were not confirmed. ``skipped_stale`` remains a field-change count.
    ``media_failed`` counts media-backed field changes dropped because their
    file could not be put into Anki's collection — the field is left alone
    rather than pointed at media that is not there.
    """

    notes_updated: int
    fields_filled: int
    tagged: int
    skipped_stale: int
    failed: int = 0
    media_failed: int = 0


def _is_empty(value: str) -> bool:
    """True iff a note field is empty for fill-only-empty purposes.

    Markup counts as FILLED: a pitch-graph SVG has no text nodes, so a
    text-only test would misread an existing graph as empty and let the
    default fill mode silently overwrite it. Sound refs alone don't count as
    content (matching ``_strip_for_dedup``); a lone ``<br>`` does — documented
    tradeoff, overwrite mode covers such fields.
    """
    text = _SOUND_REF_RE.sub("", value or "")
    if _HTML_TAG_RE.search(text):
        return False
    return _strip_for_dedup(value or "") == ""


def _is_legacy_freq_sentinel(field_key: str, value: str) -> bool:
    """True for a stored v2.7.8-v2.11.0 ``9999999`` placeholder in the sort field.

    Scoped to the one field that ever held it — the digits alone mean nothing
    anywhere else, and ``frequency`` stores rendered HTML, never a bare rank.
    """
    return field_key == "frequency_sort" and (value or "").strip() == _LEGACY_FREQ_MISS_SENTINEL


def _is_fillable(field_key: str, value: str) -> bool:
    """True when fill-only-empty mode may write ``field_key`` over ``value``.

    Empty is fillable, plus one legacy case: cards mined by v2.7.8-v2.11.0 carry
    a literal 9999999 in the sort field for words no source ranked. It is a
    placeholder, not a rank, so fill mode replaces it once a source ranks the
    word — otherwise those cards would be stuck at 9999999 forever, since
    ``_is_empty`` reads it as real content. Scoped to the one field that ever
    held it: every other field keeps plain ``_is_empty`` semantics.

    Media keys invert that adjustment. For them the ``[sound:...]`` ref IS the
    content, but ``_is_empty`` strips refs, so a fully-voiced field reads as
    empty there and fill-only mode would re-fetch and rewrite every card that
    already has audio. Any surviving ref means filled; whitespace and
    markup-only keep ``_is_empty`` semantics, so the branch narrows the
    predicate without widening it.
    """
    if field_key in _MEDIA_KEYS:
        return _is_empty(value) and not _SOUND_REF_RE.search(value or "")
    if _is_empty(value):
        return True
    return _is_legacy_freq_sentinel(field_key, value)


def _display(value: str) -> str:
    """Stripped, capped preview text for the old value (display only)."""
    text = _strip_for_dedup(value or "")
    if len(text) > _OLD_DISPLAY_CAP:
        return text[:_OLD_DISPLAY_CAP] + "…"
    return text


def _reading_from_furigana(value: str) -> str | None:
    """Recover a contiguous hiragana reading from Anki ``kanji[reading]`` furigana.

    Inverse of ``_format_furigana``: a left-to-right scan where each
    ``kanji[reading]`` group contributes its bracket content and standalone
    kana runs pass through; the Anki separator spaces (which bind a bracket to
    its own kanji run) are dropped. NOT a split-on-space —
    ``入[い]り 口[ぐち]`` must yield ``いりぐち``, not ``いぐち``. Bracket
    content is katakana-folded so the result keys hiragana-folded lookups.
    Returns ``None`` for malformed input (unbalanced brackets, empty).
    """
    text = _HTML_TAG_RE.sub("", html.unescape(value or "")).strip()
    if not text:
        return None
    if text.count("[") != text.count("]"):
        return None
    out: list[str] = []
    pos = 0
    for match in _FURIGANA_GROUP_RE.finditer(text):
        plain = text[pos : match.start()]
        if "[" in plain or "]" in plain:
            return None
        out.append(plain.replace(" ", ""))
        out.append(match.group(2))
        pos = match.end()
    tail = text[pos:]
    if "[" in tail or "]" in tail:
        return None
    out.append(tail.replace(" ", ""))
    reading = katakana_to_hiragana("".join(out))
    return reading or None


def _chunks(items: Sequence[_T], size: int) -> Iterator[Sequence[_T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _field_value(fields: dict, name: str | None) -> str | None:
    """Defensive field read (card_restyler idiom): None when absent/malformed."""
    if not name:
        return None
    entry = fields.get(name)
    if not isinstance(entry, dict):
        return None
    return entry.get("value", "") or ""


@dataclass(frozen=True)
class _NoteContext:
    """Per-note working set resolved before field computation."""

    note_id: int
    fields: dict
    mined_form: str
    reading: str  # hiragana; may be a tokenizer guess (see reading_recovered)
    reading_source: str  # "field" | "furigana" | "tokenizer"
    lemma: str
    reading_failed: bool
    lemma_failed: bool


@dataclass(frozen=True)
class _AudioWord:
    """Word-like view of a note context for ``AudioDefaults.candidates``.

    The profile ladders read a mined word's ``mined_form`` and
    ``expression_reading``; a ``_NoteContext`` recovers the same identity but
    spells the reading ``reading``. Carries exactly the three values
    ``_resolve_context`` produced — no ``lemma_reading``, because deriving one
    is the Japanese ladder's own tagger work.
    """

    mined_form: str
    expression_reading: str
    lemma: str


def scan_backfill(
    anki_service: AnkiService,
    config: AnkiMinerConfig,
    services: Any,
    options: BackfillOptions,
    *,
    expression_audio_fetcher: Any = None,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> BackfillPlan:
    """Compute every proposed backfill value for the preview table.

    ``services`` is the ``Services`` bundle from ``create_services(config)``;
    only ``definition_service`` / ``pitch_accent_service`` /
    ``frequency_service`` are read. Read-only: nothing is written to Anki.
    """
    log_summary(
        logger,
        "Backfill scan start",
        note_type=config.anki_note_type,
        fields=len(options.field_keys),
        overwrite=options.overwrite,
        deck=options.deck,
    )
    with timed_phase("backfill-scan", logger):
        return _scan_backfill_impl(
            anki_service,
            config,
            services,
            options,
            expression_audio_fetcher=expression_audio_fetcher,
            progress=progress,
            is_cancelled=is_cancelled,
        )


def _scan_backfill_impl(
    anki_service: AnkiService,
    config: AnkiMinerConfig,
    services: Any,
    options: BackfillOptions,
    *,
    expression_audio_fetcher: Any = None,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> BackfillPlan:
    anki_fields = config.anki_fields
    word_field = anki_fields.get("word")
    if not word_field:
        raise ValueError("Expression field not mapped (anki_fields['word'])")

    # Only mapped keys are actionable; an unmapped selected key is dropped
    # (never write to a "" field name).
    selected = {key for key in options.field_keys if anki_fields.get(key)}

    # Availability gating: `service is not None and is_available()` — the UI
    # enables checkboxes on field-mapping alone, so a mapped-but-service-None
    # state legitimately reaches the scan (mirrors episode_processor gates).
    unavailable: list[str] = []
    pitch_service = services.pitch_accent_service
    if selected & _PITCH_KEYS and not (pitch_service is not None and pitch_service.is_available()):
        unavailable.extend(sorted(selected & _PITCH_KEYS))
        selected -= _PITCH_KEYS
    frequency_service = services.frequency_service
    if selected & _FREQ_KEYS and not (frequency_service is not None and frequency_service.is_available()):
        unavailable.extend(sorted(selected & _FREQ_KEYS))
        selected -= _FREQ_KEYS
    definition_service = services.definition_service
    # Word audio has no is_available(): the chain is legal-but-empty when every
    # entry is disabled, so "did the caller build one" is the whole test. It is
    # an explicit parameter rather than a bundle attribute because the caller
    # owns its lifetime (it holds a live HTTP session and must be closed).
    if selected & _MEDIA_KEYS and expression_audio_fetcher is None:
        unavailable.extend(sorted(selected & _MEDIA_KEYS))
        selected -= _MEDIA_KEYS

    absent_fields = _preflight(anki_service, config, selected, word_field)
    selected -= {key for key in selected if anki_fields[key] in absent_fields}

    query = f'note:"{_escape_anki_search(config.anki_note_type)}"'
    if options.deck:
        query += f' deck:"{_escape_anki_search(options.deck)}"'
    note_ids = anki_service.find_notes(query)

    # Style-block inputs, collected once per scan (registry/SQLite I/O).
    # Every proposed miner field gets its OWN trailing block — per-field
    # self-containment, matching EpisodeProcessor._phase5_create (the old
    # single-carrier "card-wide <style>" model broke JS note types that
    # render fields in isolation).
    want_styling = bool(selected & {"definition", "glossary"})
    dict_css_entries = collect_dictionary_css_entries(config) if want_styling else []

    # get_shared_tagger stays a module attribute: the pre-existing tests patch
    # THIS name, so the ja branch must keep calling it here. config_language,
    # never the raw field — a whitelisted code with no registered profile yet
    # (ko) would otherwise reach get_tagger's ValueError and kill the scan.
    language = config_language(config)
    tagger = get_shared_tagger() if language == "ja" else get_tagger(language)
    # The word-audio ladder, resolved once per scan like the tagger (mining's
    # 1B.9 shape: service_factory hands the same callable to
    # ChainedExpressionAudioFetcher). None is Japanese and keeps
    # backfill_audio.word_audio_candidates.
    audio_candidates = get_profile(language).audio.candidates

    scanned = skipped_no_identity = identical_skips = 0
    guessed_reading_skips = reading_failures = lemma_failures = 0
    first_failed_mined_form: str | None = None
    note_plans: list[NotePlan] = []

    for chunk in _chunks(note_ids, _CHUNK):
        if is_cancelled and is_cancelled():
            break

        contexts: list[_NoteContext] = []
        for info in anki_service.notes_info(list(chunk)):
            note_id = info.get("noteId")
            fields = info.get("fields")
            if not isinstance(note_id, int) or not isinstance(fields, dict):
                scanned += 1
                continue  # deleted ({}) / malformed
            scanned += 1
            raw_word = _field_value(fields, word_field)
            mined_form = _strip_for_dedup(raw_word) if raw_word is not None else ""
            if not mined_form:
                skipped_no_identity += 1
                continue
            context = _resolve_context(note_id, fields, mined_form, anki_fields, tagger)
            reading_failures += int(context.reading_failed)
            lemma_failures += int(context.lemma_failed)
            if first_failed_mined_form is None and (context.reading_failed or context.lemma_failed):
                first_failed_mined_form = mined_form
            contexts.append(context)

        definitions, glossaries = _chunk_definition_lookups(
            definition_service,
            contexts,
            selected,
            is_cancelled=is_cancelled,
        )

        # Progress and cancellation are per NOTE, not per chunk. Every other
        # proposal is a local lookup, so a chunk-boundary tick was invisible;
        # word audio is a network round trip per note, which turns a 500-note
        # chunk into minutes of frozen bar and a Cancel that does nothing until
        # the chunk ends.
        base = scanned - len(contexts)
        cancelled = False
        for idx, ctx in enumerate(contexts):
            if is_cancelled and is_cancelled():
                cancelled = True
                break
            changes, note_identicals, note_guessed = _compute_note_changes(
                ctx,
                config,
                selected,
                options,
                pitch_service=pitch_service,
                frequency_service=frequency_service,
                definition=definitions[idx],
                glossary=glossaries[idx],
                dict_css_entries=dict_css_entries,
                expression_audio_fetcher=expression_audio_fetcher,
                tagger=tagger,
                audio_candidates=audio_candidates,
                is_cancelled=is_cancelled,
            )
            identical_skips += note_identicals
            guessed_reading_skips += note_guessed
            if changes:
                note_plans.append(NotePlan(ctx.note_id, ctx.mined_form, tuple(changes)))
            if progress:
                progress(base + idx + 1, len(note_ids))

        if cancelled:
            break

    if reading_failures or lemma_failures:
        log_summary(
            logger,
            "Backfill tokenizer degraded",
            level=logging.WARNING,
            reading_failures=reading_failures,
            lemma_failures=lemma_failures,
            mined_form=first_failed_mined_form,
        )

    plan = BackfillPlan(
        options=options,
        notes=tuple(note_plans),
        scanned=scanned,
        skipped_no_identity=skipped_no_identity,
        unavailable_fields=tuple(unavailable),
        expression_field=word_field,
        config_version=config.config_version,
        identical_skips=identical_skips,
        guessed_reading_skips=guessed_reading_skips,
        absent_fields=absent_fields,
    )
    # The one line that tells a bug report which no-op this was: query matched
    # nothing, notes matched but every target was filled, or the mapping is
    # stale. Without it a scan that proposes nothing leaves no trace at all in
    # anki_miner.log, and every failure mode reads the same to a maintainer.
    log_summary(
        logger,
        "Backfill scan",
        query=query,
        matched=len(note_ids),
        scanned=scanned,
        notes=len(plan.notes),
        fields=plan.total_field_changes,
        word_audio=sum(1 for note in plan.notes for change in note.changes if change.media_path is not None),
        overwrite=options.overwrite,
        absent=absent_fields,
        unavailable=unavailable,
        skipped_no_identity=skipped_no_identity,
        identical=identical_skips,
        guessed_reading=guessed_reading_skips,
        reading_failures=reading_failures,
        lemma_failures=lemma_failures,
    )
    return plan


def _preflight(
    anki_service: AnkiService,
    config: AnkiMinerConfig,
    selected: set[str],
    word_field: str,
) -> tuple[str, ...]:
    """Check the note type and the selected field names before scanning.

    Deliberately NOT ``AnkiService.verify_card_target`` — that validates every
    configured field including the card-type marker field, so unrelated mapping
    drift would hard-fail a backfill that never touches those fields. Mapping
    collisions and the checks needed to identify notes are fatal; unrelated
    absent selected fields are reported:

    - Word or selected targets mapped more than once → ``SetupError``;
    - note type absent from the collection → ``SetupError``;
    - the Expression field absent from the note type → ``SetupError``, since no
      note can then carry the identity every lookup and the apply-time
      staleness recheck key on;
    - the Expression field is not the note type's first field → ``SetupError``;
    - any other selected field absent → returned by name, its key dropped by
      the caller. The remaining groups still run.

    At most two AnkiConnect calls per scan, both before the note loop.
    """
    anki_fields = config.anki_fields
    targets = [word_field, *(anki_fields[key] for key in selected if anki_fields[key])]
    collision_error = field_target_collision_message(config.anki_note_type, targets)
    if collision_error:
        raise SetupError(collision_error)

    note_types = anki_service.note_type_names()
    if config.anki_note_type not in note_types:
        logger.warning(
            "Backfill preflight failed: note_type=%s available=%d",
            config.anki_note_type,
            len(note_types),
        )
        raise SetupError(missing_note_type_message(config.anki_note_type, note_types))

    ordered_actual = anki_service.ordered_note_type_field_names(config.anki_note_type)
    actual = set(ordered_actual)
    mapping_error = field_mapping_error(
        config.anki_note_type,
        ordered_actual,
        {word_field},
        word_field,
    )
    if mapping_error:
        logger.warning(
            "Backfill preflight failed: note_type=%s field=%s fields=%d",
            config.anki_note_type,
            word_field,
            len(actual),
        )
        raise SetupError(mapping_error)

    return tuple(sorted({anki_fields[key] for key in selected if anki_fields[key] not in actual}))


def _resolve_context(
    note_id: int,
    fields: dict,
    mined_form: str,
    anki_fields: Mapping[str, str],
    tagger: Any,
) -> _NoteContext:
    """Recover the (reading, lemma) identity the lookup recipes key on.

    Reading ladder: (a) the stored expression_reading field; (b) parsed from
    the ExpressionFurigana brackets; (c) a context-free tokenizer reading —
    LOOKUP-ONLY, never persisted to the card (a homograph guess must not
    become durable data).
    """
    reading = ""
    reading_source = "tokenizer"
    reading_failed = False
    stored = _field_value(fields, anki_fields.get("expression_reading"))
    if stored and not _is_empty(stored):
        reading = katakana_to_hiragana(_strip_for_dedup(stored))
        reading_source = "field"
    else:
        furigana = _field_value(fields, anki_fields.get("expression_furigana"))
        if furigana and not _is_empty(furigana):
            parsed = _reading_from_furigana(furigana)
            if parsed:
                reading = parsed
                reading_source = "furigana"
    if not reading:
        try:
            reading = katakana_to_hiragana(generate_reading(mined_form, tagger))
        except Exception:  # pragma: no cover - tagger failure is environmental
            reading = ""
            reading_failed = True
        reading_source = "tokenizer"

    lemma = mined_form
    lemma_failed = False
    try:
        tokens = list(tagger(mined_form))
        if len(tokens) == 1:
            lemma = extract_lemma(tokens[0]) or mined_form
    except Exception:  # pragma: no cover - tagger failure is environmental
        lemma_failed = True

    return _NoteContext(
        note_id,
        fields,
        mined_form,
        reading,
        reading_source,
        lemma,
        reading_failed,
        lemma_failed,
    )


def _chunk_definition_lookups(
    definition_service: Any,
    contexts: list[_NoteContext],
    selected: set[str],
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[list[str | None], list[str | None]]:
    """Batch the chunk's definition/glossary lookups (the _phase4 recipe).

    Includes the miss-only lemma retry for glossaries and the
    mined_form → (lemma, None) fallback context for definitions.
    """
    definitions: list[str | None] = [None] * len(contexts)
    glossaries: list[str | None] = [None] * len(contexts)
    if definition_service is None:
        return definitions, glossaries

    for key, results in (("definition", definitions), ("glossary", glossaries)):
        if is_cancelled and is_cancelled():
            break
        if key not in selected:
            continue
        idx_map: list[int] = []
        pairs: list[tuple[str, str | None]] = []
        fallback_context: dict[str, tuple[str, str | None]] = {}
        for i, ctx in enumerate(contexts):
            idx_map.append(i)
            pairs.append((ctx.mined_form, ctx.reading or None))
            fallback_context.setdefault(ctx.mined_form, (ctx.lemma, None))
        if not pairs:
            continue
        if key == "definition":
            found = definition_service.get_definitions_batch(
                pairs,
                None,
                fallback_context,
                is_cancelled=is_cancelled,
            )
        else:
            found = definition_service.get_glossaries_batch(
                pairs,
                None,
                is_cancelled=is_cancelled,
            )
            # Miss-only lemma retry (mirrors _phase4: get_glossaries_batch has
            # no fallback mechanism of its own).
            retry_idx = [
                j
                for j, g in enumerate(found)
                if not g and contexts[idx_map[j]].lemma != contexts[idx_map[j]].mined_form
            ]
            if retry_idx:
                retry_pairs: list[tuple[str, str | None]] = [
                    (contexts[idx_map[j]].lemma, contexts[idx_map[j]].reading or None) for j in retry_idx
                ]
                retried = definition_service.get_glossaries_batch(
                    retry_pairs,
                    None,
                    is_cancelled=is_cancelled,
                )
                for j, g in zip(retry_idx, retried, strict=True):
                    found[j] = g
        for j, value in enumerate(found):
            results[idx_map[j]] = value

    return definitions, glossaries


def _compute_note_changes(
    ctx: _NoteContext,
    config: AnkiMinerConfig,
    selected: set[str],
    options: BackfillOptions,
    *,
    pitch_service: Any,
    frequency_service: Any,
    definition: str | None,
    glossary: str | None,
    dict_css_entries: list[tuple[str, str, str]],
    expression_audio_fetcher: Any = None,
    tagger: Any = None,
    audio_candidates: Callable[[Any], list[tuple[str, str]]] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[list[FieldChange], int, int]:
    """Emit FieldChanges for one note under the fill/overwrite policy.

    Returns ``(changes, identical_skips, guessed_reading_skips)`` — the second
    element counts overwrite-mode fields skipped because the proposal matched
    the stored value byte-for-byte, the third those skipped because the pitch
    render would have come off a guessed reading (see the loop below).
    """
    anki_fields = config.anki_fields
    proposals: dict[str, str] = {}
    media_paths: dict[str, Path] = {}

    if selected & _PITCH_KEYS:
        proposals.update(_pitch_proposals(ctx, config, selected, pitch_service))

    if selected & _FREQ_KEYS:
        proposals.update(_frequency_proposals(ctx, selected, frequency_service))

    if "definition" in selected and definition:
        proposals["definition"] = definition
    if "glossary" in selected and glossary:
        proposals["glossary"] = glossary

    # Reading group: pure cross-fill. expression_reading only from a
    # furigana-recovered reading; expression_furigana only from a stored
    # reading field. Tokenizer readings are never persisted.
    if "expression_reading" in selected and ctx.reading_source == "furigana":
        proposals["expression_reading"] = html.escape(ctx.reading)
    if "expression_furigana" in selected and ctx.reading_source == "field":
        proposals["expression_furigana"] = html.escape(_format_furigana(ctx.mined_form, ctx.reading))

    # Style block: every freshly-proposed miner field carries its OWN trailing
    # block, tree-shaken against that field alone and with its dict CSS
    # filtered to the dictionaries present in it — byte-identical to what
    # mining would write (attach_card_style_block enforces the trailing /
    # never-leading placement and no-ops on markup-less proposals). No
    # cross-field gate: the other field's styling is irrelevant to this one on
    # field-isolating note types, and a divergent block here would make the
    # restyler churn backfilled cards forever. Proposals are fresh renders, so
    # they are born stamped — no stamping needed (that's the restyler's job
    # for legacy bodies).
    for key in ("definition", "glossary"):
        if key in proposals:
            proposals[key] = attach_card_style_block(proposals[key], dict_css_entries=dict_css_entries)

    # Word audio, after the style-block loop (it carries no markup to stamp).
    # Fillability is checked HERE, before the fetch, rather than left to the
    # policy loop below: this is the only proposal that costs a network round
    # trip, so a card that already has audio must not pay for one in fill-only
    # mode. The policy loop still re-checks, so the gate is not load-bearing
    # for correctness — only for cost.
    if "expression_audio" in selected and expression_audio_fetcher is not None:
        audio_field = anki_fields.get("expression_audio")
        current_audio = _field_value(ctx.fields, audio_field) if audio_field else None
        if current_audio is not None and (options.overwrite or _is_fillable("expression_audio", current_audio)):
            audio_path = _audio_proposal(
                ctx,
                expression_audio_fetcher,
                tagger,
                is_cancelled,
                audio_candidates=audio_candidates,
            )
            if audio_path is not None:
                proposals["expression_audio"] = f"[sound:{audio_path.name}]"
                media_paths["expression_audio"] = audio_path

    changes: list[FieldChange] = []
    identical_skips = 0
    guessed_reading_skips = 0
    for key in sorted(proposals):
        new_value = proposals[key]
        if not new_value:
            continue
        field_name = anki_fields.get(key)
        if not field_name:
            continue
        current = _field_value(ctx.fields, field_name)
        if current is None:
            continue  # field absent on this note-type instance
        if options.overwrite:
            if key in _PITCH_KEYS and ctx.reading_source == "tokenizer" and not _is_empty(current):
                # Pitch markup lays the accent position over THIS reading's
                # morae, so the reading decides the output even when the
                # position lookup is right. A tokenizer reading is a
                # context-free homograph guess (generate_reading("弾く") is ひく
                # where mining, with the sentence, read はじく) — which is why
                # _resolve_context calls it lookup-only, never persisted.
                # Overwriting a populated pitch field from one persists it, and
                # would silently replace a correct card with a wrong-homograph
                # one. Fill mode is unaffected: it only writes empty fields, so
                # a guess there still beats nothing.
                guessed_reading_skips += 1
                continue
            if new_value == current:
                identical_skips += 1
                continue
        elif not _is_fillable(key, current):
            continue
        changes.append(FieldChange(key, field_name, _display(current), new_value, media_paths.get(key)))

    # Erase a legacy 9999999 the lookup could not replace. Every other write
    # here is a value the code computed, so a word no source ranks proposes
    # nothing and the stored placeholder survives — in BOTH modes, since
    # _is_fillable only decides whether a write is *permitted*, never that one
    # happens. That left the sentinel unreachable: ranked words got a real rank,
    # unranked ones kept 9999999 forever. The empty new_value is a real write,
    # not a no-op to be filtered out (apply_backfill relies on that).
    #
    # Deliberately narrow. A genuine rank on a now-unranked word is left alone,
    # and so is the display field — Backfill does not wipe values the user has
    # just because the current chain no longer produces them. The scan's
    # is_available() gate above means an unloaded chain never reaches here.
    if "frequency_sort" in selected and "frequency_sort" not in proposals:
        sort_field = anki_fields.get("frequency_sort")
        current = _field_value(ctx.fields, sort_field) if sort_field else None
        if sort_field and current is not None and _is_legacy_freq_sentinel("frequency_sort", current):
            changes.append(FieldChange("frequency_sort", sort_field, _display(current), ""))

    return changes, identical_skips, guessed_reading_skips


def _pitch_proposals(
    ctx: _NoteContext,
    config: AnkiMinerConfig,
    selected: set[str],
    pitch_service: Any,
) -> dict[str, str]:
    """Pitch graph/text values, lemma-keyed with a mined_form retry.

    Lemma stays the primary key (the mining pipeline's pitch invariant). The
    reading-scoped mined_form retry is an intentional BACKFILL-ONLY coverage
    extension: mining has the contextual lemma, backfill re-derives it from the
    card front and may miss on rare forms — the retry recovers those while the
    stored reading keeps homographs disambiguated.
    """
    if not ctx.reading:
        return {}
    position: str | None
    key_used = ctx.lemma
    position, _category = pitch_service.lookup_detailed(ctx.lemma, ctx.reading, None, config.pitch_category_format)
    if not position and ctx.lemma != ctx.mined_form:
        key_used = ctx.mined_form
        position, _category = pitch_service.lookup_detailed(
            ctx.mined_form, ctx.reading, None, config.pitch_category_format
        )
    if not position:
        return {}

    proposals: dict[str, str] = {}
    entry = pitch_service.lookup_entry(key_used, ctx.reading)
    nasal = entry.nasal if entry else ()
    devoice = entry.devoice if entry else ()
    if "pitch_graph" in selected:
        graph_html = render_pitch_graph_field(position, ctx.reading)
        if graph_html:
            proposals["pitch_graph"] = graph_html
    if "pitch_text" in selected:
        text_html = render_pitch_text_field(position, ctx.reading, nasal, devoice)
        if text_html:
            proposals["pitch_text"] = text_html
    return proposals


def _frequency_proposals(
    ctx: _NoteContext,
    selected: set[str],
    frequency_service: Any,
) -> dict[str, str]:
    """Frequency display/sort values (the _phase2 recipe).

    Keyed on mined_form + hiragana reading with the WHOLE-RESULT miss-only
    lemma fallback (never per-source — Issues #19/#5; see the long rationale
    in EpisodeProcessor._phase2_filter, mirrored here). A miss proposes NO sort
    value at all, mirroring mining: the field never claims a rank the word
    does not have.
    """
    sources = frequency_service.lookup_all(ctx.mined_form, ctx.reading)
    if (
        not sources
        and ctx.lemma
        and ctx.lemma != ctx.mined_form
        and _differs_by_okurigana_only(ctx.mined_form, ctx.lemma)
    ):
        sources = frequency_service.lookup_all(ctx.lemma, ctx.reading)

    proposals: dict[str, str] = {}
    if "frequency" in selected and sources:
        rendered = render_frequency_html(sources)
        if rendered:
            proposals["frequency"] = rendered
    if "frequency_sort" in selected:
        rank = harmonic_rank(sources)
        if rank is not None:
            proposals["frequency_sort"] = str(rank)
    return proposals


def _audio_proposal(
    ctx: _NoteContext,
    fetcher: Any,
    tagger: Any,
    is_cancelled: Callable[[], bool] | None,
    *,
    audio_candidates: Callable[[Any], list[tuple[str, str]]] | None = None,
) -> Path | None:
    """Resolve word audio for one note through the configured chain.

    Returns the local cached file, or None when no enabled source has this
    word. The fetcher protocol forbids raising, so there is no try/except here
    — the same contract the mining loop relies on (``AudioStage._per_item``).

    Deliberately no reading-provenance guard, unlike pitch: a tokenizer-guessed
    reading is allowed to fetch and to overwrite. JPod101 refuses a non-kana
    reading outright and a wrong kana reading misses rather than fetching a
    homograph; a synthetic source will voice the guess, which is the accepted
    cost of the feature.

    ``audio_candidates`` is the active profile's ladder builder
    (``AudioDefaults.candidates``); None is Japanese and keeps the kana ladder
    ``backfill_audio`` builds from the recovered (reading, lemma) pair, which
    is also the only branch that consults ``tagger``.
    """
    if audio_candidates is not None:
        candidates = audio_candidates(_AudioWord(ctx.mined_form, ctx.reading, ctx.lemma))
    else:
        candidates = word_audio_candidates(ctx.mined_form, ctx.reading, ctx.lemma, tagger)
    if not candidates:
        return None
    path: Path | None = fetcher.fetch_candidates(candidates, cancelled_check=is_cancelled)
    return path


def apply_backfill(
    anki_service: AnkiService,
    plan: BackfillPlan,
    *,
    tag: str = BACKFILL_TAG,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> BackfillResult:
    """Write the plan's precomputed values, recheck staleness, tag touched notes.

    What the user previewed is exactly what gets written — values are never
    recomputed. Per chunk, one ``notesInfo`` recheck drops notes deleted since
    the scan, notes whose normalized Expression no longer matches the scanned
    identity, and (in fill-only-empty mode) changes whose target field is no
    longer empty. Overwrite mode may replace targets, but never bypasses the
    Expression identity check. Tags are added only to IDs whose field update
    AnkiConnect confirmed; a tag failure is logged and reflected in ``tagged``,
    never fatal.

    Cancellation is honored between chunks: committed chunks stay written and
    tagged (the restyler precedent); partial counts are returned.
    """
    log_summary(
        logger,
        "Backfill apply start",
        planned=len(plan.notes),
        fields=plan.total_field_changes,
        overwrite=plan.options.overwrite,
    )
    with timed_phase("backfill-apply", logger):
        return _apply_backfill_impl(
            anki_service,
            plan,
            tag=tag,
            progress=progress,
            is_cancelled=is_cancelled,
        )


def _apply_backfill_impl(
    anki_service: AnkiService,
    plan: BackfillPlan,
    *,
    tag: str = BACKFILL_TAG,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> BackfillResult:
    overwrite = plan.options.overwrite
    total_notes = len(plan.notes)
    notes_updated = fields_filled = tagged = skipped_stale = failed = media_failed = 0
    written_so_far = 0

    for chunk in _chunks(plan.notes, _CHUNK):
        if is_cancelled and is_cancelled():
            break
        infos = {
            info.get("noteId"): info.get("fields")
            for info in anki_service.notes_info([note.note_id for note in chunk])
            if isinstance(info, dict) and isinstance(info.get("noteId"), int)
        }
        # Upload this chunk's media once, before any note is written. Restricted
        # to notes that survived the notesInfo recheck so a note deleted between
        # scan and apply cannot leave an unreferenced file in the collection.
        media_sources: dict[str, Path] = {}
        for note in chunk:
            if note.note_id not in infos:
                continue
            for change in note.changes:
                if change.media_path is None:
                    continue
                if not change.media_path.exists():
                    # Cached file deleted between scan and apply.
                    media_failed += 1
                    continue
                media_sources.setdefault(change.media_path.name, change.media_path)
        stored_names = anki_service.store_media_files(media_sources) if media_sources else {}

        updates: list[tuple[int, dict[str, str]]] = []
        for note in chunk:
            fields = infos.get(note.note_id)
            if not isinstance(fields, dict):
                skipped_stale += len(note.changes)  # deleted since scan
                continue
            if plan.expression_field:
                current_expression = _field_value(fields, plan.expression_field)
                if current_expression is None or _strip_for_dedup(current_expression) != note.expression:
                    skipped_stale += len(note.changes)
                    continue
            payload: dict[str, str] = {}
            for change in note.changes:
                current = _field_value(fields, change.field_name)
                if current is None:
                    skipped_stale += 1
                    continue
                if not overwrite and not _is_fillable(change.field_key, current):
                    skipped_stale += 1
                    continue
                value = change.new_value
                if change.media_path is not None:
                    if not change.media_path.exists():
                        # Already counted by the pre-pass above; counting it
                        # again here would double-report the same file.
                        continue
                    # The [sound:...] name is only knowable after the upload
                    # confirms it (media names are content-addressed). An
                    # unconfirmed file must never be referenced — that is how a
                    # card ends up pointing at missing media.
                    confirmed = stored_names.get(change.media_path.name)
                    if confirmed is None:
                        media_failed += 1
                        continue
                    value = f"[sound:{confirmed}]"
                payload[change.field_name] = value
            if payload:
                updates.append((note.note_id, payload))
        written_so_far += len(chunk)
        if updates:
            successful_id_set = set(anki_service.update_notes_fields(updates))
            confirmed_updates = [(nid, payload) for nid, payload in updates if nid in successful_id_set]
            confirmed_ids = [nid for nid, _payload in confirmed_updates]
            notes_updated += len(confirmed_ids)
            fields_filled += sum(len(payload) for _nid, payload in confirmed_updates)
            failed += len(updates) - len(confirmed_ids)
            if confirmed_ids:
                try:
                    anki_service.add_tags(confirmed_ids, tag)
                    tagged += len(confirmed_ids)
                except Exception as e:
                    logger.warning("Backfill tagging failed for %d note(s): %s", len(confirmed_ids), e)
        if progress:
            progress(written_so_far, total_notes)

    log_summary(
        logger,
        "Backfill apply",
        planned=total_notes,
        processed=written_so_far,
        notes=notes_updated,
        fields=fields_filled,
        tagged=tagged,
        stale=skipped_stale,
        failed=failed,
    )
    return BackfillResult(
        notes_updated=notes_updated,
        fields_filled=fields_filled,
        tagged=tagged,
        skipped_stale=skipped_stale,
        failed=failed,
        media_failed=media_failed,
    )

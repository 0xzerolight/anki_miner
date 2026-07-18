"""Bulk-fill fields on existing miner cards from currently installed resources.

The Card Backfill tool (Tools → Card Backfill) generalizes the card restyler's
enumerate → chunk → ``notesInfo`` → compute → ``updateNoteFields`` loop
(``card_restyler.restyle_mined_cards``): after the user installs a pitch CSV,
frequency sources, or dictionaries, it proposes values for pitch graph/text,
frequency display/sort, definition, glossary, and reading/furigana fields that
old cards are missing.

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
from typing import TYPE_CHECKING, Any, TypeVar

from anki_miner.services.anki_note_builder import (
    _HTML_TAG_RE,
    _SOUND_REF_RE,
    _strip_for_dedup,
)

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
from anki_miner.services.tagger import get_shared_tagger
from anki_miner.utils.text_utils import (
    _format_furigana,
    generate_reading,
    katakana_to_hiragana,
)

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
}

_PITCH_KEYS = frozenset(FIELD_GROUPS["pitch"])
_FREQ_KEYS = frozenset(FIELD_GROUPS["frequency"])
_FREQ_MISS_SENTINEL = "9999999"
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
    # Sort-field writes proposed on a frequency MISS (the 9999999 sentinel,
    # mining-faithful). Surfaced separately so the summary doesn't read as
    # "N cards ranked" when many only received the sentinel.
    sentinel_only_sorts: int

    @property
    def total_field_changes(self) -> int:
        return sum(len(note.changes) for note in self.notes)


@dataclass(frozen=True)
class BackfillResult:
    """Apply outcome. ``tagged``/``fields_filled`` count ATTEMPTED updates —
    ``update_notes_fields`` returns only a count, so the tag marks notes the
    backfill touched or attempted."""

    notes_updated: int
    fields_filled: int
    tagged: int
    skipped_stale: int


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


def scan_backfill(
    anki_service: AnkiService,
    config: AnkiMinerConfig,
    services: Any,
    options: BackfillOptions,
    *,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> BackfillPlan:
    """Compute every proposed backfill value for the preview table.

    ``services`` is the ``Services`` bundle from ``create_services(config)``;
    only ``definition_service`` / ``pitch_accent_service`` /
    ``frequency_service`` are read. Read-only: nothing is written to Anki.
    """
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

    tagger = get_shared_tagger()

    scanned = skipped_no_identity = sentinel_only_sorts = 0
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
            contexts.append(_resolve_context(note_id, fields, mined_form, anki_fields, tagger))

        definitions, glossaries = _chunk_definition_lookups(definition_service, contexts, selected)

        for idx, ctx in enumerate(contexts):
            changes = _compute_note_changes(
                ctx,
                config,
                selected,
                options,
                pitch_service=pitch_service,
                frequency_service=frequency_service,
                definition=definitions[idx],
                glossary=glossaries[idx],
                dict_css_entries=dict_css_entries,
            )
            sentinel_only_sorts += sum(
                1 for c in changes if c.field_key == "frequency_sort" and c.new_value == _FREQ_MISS_SENTINEL
            )
            if changes:
                note_plans.append(NotePlan(ctx.note_id, ctx.mined_form, tuple(changes)))

        if progress:
            progress(scanned, len(note_ids))

    return BackfillPlan(
        options=options,
        notes=tuple(note_plans),
        scanned=scanned,
        skipped_no_identity=skipped_no_identity,
        unavailable_fields=tuple(unavailable),
        sentinel_only_sorts=sentinel_only_sorts,
    )


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
        reading_source = "tokenizer"

    lemma = mined_form
    try:
        tokens = list(tagger(mined_form))
        if len(tokens) == 1:
            lemma = extract_lemma(tokens[0]) or mined_form
    except Exception:  # pragma: no cover - tagger failure is environmental
        pass

    return _NoteContext(note_id, fields, mined_form, reading, reading_source, lemma)


def _chunk_definition_lookups(
    definition_service: Any,
    contexts: list[_NoteContext],
    selected: set[str],
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
            found = definition_service.get_definitions_batch(pairs, None, fallback_context)
        else:
            found = definition_service.get_glossaries_batch(pairs, None)
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
                retried = definition_service.get_glossaries_batch(retry_pairs, None)
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
    dict_css_entries: list[tuple[str, str]],
) -> list[FieldChange]:
    """Emit FieldChanges for one note under the fill/overwrite policy."""
    anki_fields = config.anki_fields
    proposals: dict[str, str] = {}

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

    changes: list[FieldChange] = []
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
            if new_value == current:
                continue
        elif not _is_empty(current):
            continue
        changes.append(FieldChange(key, field_name, _display(current), new_value))
    return changes


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
    in EpisodeProcessor._phase2_filter, mirrored here). The sort field's
    9999999 miss-sentinel is mining-faithful (sorts unranked words last).
    """
    sources = frequency_service.lookup_all(ctx.mined_form, ctx.reading)
    if not sources and ctx.lemma and ctx.lemma != ctx.mined_form:
        sources = frequency_service.lookup_all(ctx.lemma, ctx.reading)

    proposals: dict[str, str] = {}
    if "frequency" in selected and sources:
        rendered = render_frequency_html(sources)
        if rendered:
            proposals["frequency"] = rendered
    if "frequency_sort" in selected:
        rank = harmonic_rank(sources)
        proposals["frequency_sort"] = str(rank) if rank is not None else _FREQ_MISS_SENTINEL
    return proposals


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
    the scan and (in fill-only-empty mode) drops any change whose target field
    is no longer empty, so a value the user added between Scan and Apply is
    never clobbered; overwrite mode only drops deleted notes (the user asked
    for the replace). Tags are added per chunk immediately AFTER that chunk's
    write, so cancellation never leaves written-but-untagged notes; a tag
    failure is logged and reflected in ``tagged``, never fatal.

    Cancellation is honored between chunks: committed chunks stay written and
    tagged (the restyler precedent); partial counts are returned.
    """
    overwrite = plan.options.overwrite
    total_notes = len(plan.notes)
    notes_updated = fields_filled = tagged = skipped_stale = 0
    written_so_far = 0

    for chunk in _chunks(plan.notes, _CHUNK):
        if is_cancelled and is_cancelled():
            break
        infos = {
            info.get("noteId"): info.get("fields")
            for info in anki_service.notes_info([note.note_id for note in chunk])
            if isinstance(info, dict) and isinstance(info.get("noteId"), int)
        }
        updates: list[tuple[int, dict[str, str]]] = []
        for note in chunk:
            fields = infos.get(note.note_id)
            if not isinstance(fields, dict):
                skipped_stale += len(note.changes)  # deleted since scan
                continue
            payload: dict[str, str] = {}
            for change in note.changes:
                current = _field_value(fields, change.field_name)
                if current is None:
                    skipped_stale += 1
                    continue
                if not overwrite and not _is_empty(current):
                    skipped_stale += 1
                    continue
                payload[change.field_name] = change.new_value
            if payload:
                updates.append((note.note_id, payload))
        written_so_far += len(chunk)
        if updates:
            notes_updated += anki_service.update_notes_fields(updates)
            fields_filled += sum(len(payload) for _nid, payload in updates)
            attempted_ids = [nid for nid, _payload in updates]
            try:
                anki_service.add_tags(attempted_ids, tag)
                tagged += len(attempted_ids)
            except Exception as e:
                logger.warning("Backfill tagging failed for %d note(s): %s", len(attempted_ids), e)
        if progress:
            progress(written_so_far, total_notes)

    return BackfillResult(
        notes_updated=notes_updated,
        fields_filled=fields_filled,
        tagged=tagged,
        skipped_stale=skipped_stale,
    )

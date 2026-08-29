"""Filter a premade Anki deck into a new deck through the app's word filters.

The Deck Filter tool (Utilities → Deck Filter): the user imports a premade
shared deck into Anki, points the tool at it, and gets a NEW deck containing
only the notes that survive the app's filtering stack — known-words
subtraction (including the Issue #42 user ignore list), frequency band,
blacklist/whitelist, script type, and name wordsets. The source deck is never
modified; kept notes are copied verbatim (same model, same field values —
same collection, so ``<img>``/``[sound:]`` refs resolve without re-upload).

Two phases, both GUI-free and cancellable, mirroring ``card_backfiller``:

- :func:`scan_deck_filter` (read-only) resolves each note's expression to a
  synthetic :class:`TokenizedWord` and runs the reusable subset of
  ``WordFilterService`` over it, producing a :class:`DeckFilterPlan` with
  per-reason drop counts.
- :func:`apply_deck_filter` creates the target deck and copies the plan's
  kept notes via ``AnkiService.add_notes_raw``.

Deliberate divergences from ``EpisodeProcessor._phase2_filter``:

- Corpus-context filters are skipped: i+1 (needs a line index), sentence
  length (duration is always 0 here), sentence dedup (a deck without a
  sentence field would collapse to one note — all empty sentences share one
  dedup key), episode-count (each note appears once by construction), and
  offline-definition-existence (a premade deck already carries definitions).
- Known-words reads are READ-ONLY: no ``sync_with_anki`` (that path writes
  the DB; a scan must not).
- The existing-vocabulary set negates the source deck
  (``get_vocabulary_excluding_deck``) — otherwise every note in the deck
  would be "known" against itself. Known limitation: forms the DB already
  absorbed from a previous sync while the source deck was included cannot be
  told apart; the ``known`` drop count makes that visible.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from anki_miner.languages.registry import config_language, get_profile
from anki_miner.models import TokenizedWord
from anki_miner.services.card_backfiller import (
    _chunks,
    _escape_anki_search,
    _field_value,
    _strip_for_dedup,
)
from anki_miner.services.frequency.multi_frequency_service import harmonic_rank, min_rank
from anki_miner.services.morphology import extract_lemma
from anki_miner.services.subtitle_parser import _differs_by_okurigana_only
from anki_miner.services.word_filter import enabled_script_options
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.text_utils import generate_reading, katakana_to_hiragana

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.anki_service import AnkiService

logger = logging.getLogger(__name__)

DECKFILTER_TAG = "anki-miner::deckfilter"
_NOTES_CHUNK = 500
_ADD_CHUNK = 100

# Drop reasons in application order. Stable keys — the tab maps them to
# translated labels; the plan stores (key, count) pairs with zero counts
# omitted.
DROP_REASONS: tuple[str, ...] = (
    "no_expression",
    "not_japanese",
    "duplicate_in_source",
    "known",
    "unranked",
    "frequency_band",
    "blacklist",
    "script_type",
    "name_wordset",
)


@dataclass(frozen=True)
class DeckFilterOptions:
    """User selections for one scan. ``None`` field = per-note first field
    for the expression, "generate"/"absent" for reading/sentence."""

    source_deck: str
    target_deck: str
    expression_field: str | None = None
    reading_field: str | None = None
    sentence_field: str | None = None


@dataclass(frozen=True)
class DeckInspection:
    """Cheap pre-scan probe of the source deck, for the field pickers."""

    note_count: int
    models: tuple[str, ...]  # dominant (most notes) first
    field_names: tuple[str, ...]  # union; dominant model's order first
    first_field_by_model: Mapping[str, str]


@dataclass(frozen=True)
class KeptNote:
    """One note that survived filtering — everything apply needs to copy it."""

    note_id: int
    model_name: str
    fields: dict[str, str]  # raw scan-time values incl. media refs
    tags: tuple[str, ...]
    expression: str  # dedup-normalized
    reading: str
    frequency_rank: int | None
    forced: bool  # whitelist force-include


@dataclass(frozen=True)
class DeckFilterPlan:
    """Scan output: what apply would copy, plus the per-reason drop counts."""

    options: DeckFilterOptions
    kept: tuple[KeptNote, ...]
    drops: tuple[tuple[str, int], ...]  # (reason key, count), zero counts omitted
    scanned: int
    forced_count: int
    config_version: int


@dataclass(frozen=True)
class DeckFilterResult:
    """Apply outcome. ``not_created`` counts null addNotes slots among
    attempted notes; notes never attempted (cancel) appear in neither."""

    created: int
    not_created: int


def inspect_deck(
    anki_service: AnkiService,
    source_deck: str,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> DeckInspection:
    """Probe the source deck's models and field names for the pickers."""
    note_ids = anki_service.find_notes(f'deck:"{_escape_anki_search(source_deck)}"')
    model_counts: Counter[str] = Counter()
    fields_by_model: dict[str, tuple[str, ...]] = {}
    for chunk in _chunks(note_ids, _NOTES_CHUNK):
        if is_cancelled and is_cancelled():
            break
        for note in anki_service.notes_info(list(chunk)):
            fields = note.get("fields")
            model = note.get("modelName")
            if not isinstance(fields, dict) or not fields or not isinstance(model, str):
                continue
            model_counts[model] += 1
            if model not in fields_by_model:
                # notesInfo preserves field order; first note of a model is
                # representative for the whole model.
                fields_by_model[model] = tuple(fields)
    models = tuple(model for model, _ in model_counts.most_common())
    union: list[str] = []
    for model in models:
        for name in fields_by_model[model]:
            if name not in union:
                union.append(name)
    first_field_by_model = {model: fields_by_model[model][0] for model in models}
    return DeckInspection(
        note_count=len(note_ids),
        models=models,
        field_names=tuple(union),
        first_field_by_model=first_field_by_model,
    )


@dataclass(frozen=True)
class _Candidate:
    """Scan-time pairing of a synthesized word with its source note."""

    note_id: int
    model_name: str
    fields: dict[str, str]
    tags: tuple[str, ...]


def _synthesize_word(
    expression: str,
    fields: dict,
    options: DeckFilterOptions,
    tagger: Any,
) -> TokenizedWord:
    """Build the TokenizedWord the filters run on.

    ``pos=None`` + ``orth_base=""`` makes ``mined_form`` return ``surface``
    verbatim (``select_mined_form``) — the card keeps the spelling it has.
    Reading ladder (the ``card_backfiller._resolve_context`` recipe minus the
    furigana rung — a foreign note type has no known furigana field): picked
    reading field, else a context-free tokenizer reading (lookup-only). Lemma
    only from a single-token tagger parse; a multi-token expression keeps
    ``lemma == expression`` so the kana-variant fold cannot misfire.
    """
    reading = ""
    stored = _field_value(fields, options.reading_field)
    if stored:
        reading = katakana_to_hiragana(_strip_for_dedup(stored))
    if not reading and tagger is not None:
        try:
            reading = katakana_to_hiragana(generate_reading(expression, tagger))
        except Exception:  # pragma: no cover - tagger failure is environmental
            reading = ""

    lemma = expression
    if tagger is not None:
        try:
            tokens = list(tagger(expression))
            if len(tokens) == 1:
                lemma = extract_lemma(tokens[0]) or expression
        except Exception:  # pragma: no cover - tagger failure is environmental
            pass

    sentence_value = _field_value(fields, options.sentence_field)
    sentence = _strip_for_dedup(sentence_value) if sentence_value else ""

    return TokenizedWord(
        surface=expression,
        lemma=lemma,
        reading=reading,
        sentence=sentence,
        start_time=0.0,
        end_time=0.0,
        duration=0.0,
        pos=None,
        orth_base="",
        expression_reading=reading,
    )


def _collect_known_forms(
    anki_service: AnkiService,
    config: AnkiMinerConfig,
    services: Any,
    source_deck: str,
) -> set[str]:
    """Union of every form that counts as "already known" for this scan.

    Read-only mirror of the ``_phase2_filter`` known-set recipe: the user
    ignore list is ALWAYS applied (Issue #42), the DB cache only when
    ``use_known_words_db`` — but never ``sync_with_anki`` (a scan must not
    write). Guarded reads degrade like the mining path; the Anki vocab query
    is NOT degraded — a wrong answer here silently keeps the whole source
    deck, so ``get_vocabulary_excluding_deck`` raises instead.
    """
    known: set[str] = set()
    known_word_db = getattr(services, "known_word_db", None)
    if known_word_db is not None and known_word_db.is_available():
        try:
            known |= known_word_db.get_words_by_source("user")
        except (sqlite3.Error, OSError) as e:
            logger.warning("Could not read the user ignore list from known_words.db (%s); proceeding without it.", e)
        if config.use_known_words_db:
            try:
                known |= known_word_db.get_known_words()
            except (sqlite3.Error, OSError) as e:
                logger.warning("Could not read known_words.db (%s); using Anki vocabulary only.", e)
    known |= anki_service.get_vocabulary_excluding_deck(source_deck)
    return known


def _attach_frequency(words: list[TokenizedWord], frequency_service: Any) -> None:
    """Attach per-source ranks (the ``_phase2_filter`` recipe, in-place).

    Keyed on ``(mined_form, reading)``; whole-result miss-only lemma fallback
    fires only for an okurigana-only alternate over the same kanji stem — a
    different-kanji lemma may be another homograph (Issues #19/#5).
    """
    pairs: list[tuple[str, str | None]] = [
        (word.mined_form, katakana_to_hiragana(word.expression_reading or word.reading) or None) for word in words
    ]
    all_sources = frequency_service.lookup_all_many(pairs)
    fallback_indexes = [
        i
        for i, (word, sources) in enumerate(zip(words, all_sources, strict=True))
        if not sources
        and word.lemma
        and word.lemma != word.mined_form
        and _differs_by_okurigana_only(word.mined_form, word.lemma)
    ]
    if fallback_indexes:
        fallback_pairs: list[tuple[str, str | None]] = [
            (words[i].lemma, katakana_to_hiragana(words[i].reading) or None) for i in fallback_indexes
        ]
        for i, sources in zip(fallback_indexes, frequency_service.lookup_all_many(fallback_pairs), strict=True):
            all_sources[i] = sources
    for word, sources in zip(words, all_sources, strict=True):
        word.frequency_sources = sources
        word.frequency_rank = min_rank(sources)
        word.frequency_harmonic_rank = harmonic_rank(sources)


def scan_deck_filter(
    anki_service: AnkiService,
    config: AnkiMinerConfig,
    services: Any,
    options: DeckFilterOptions,
    *,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> DeckFilterPlan:
    """Read the source deck and compute which notes survive the filters.

    ``services`` is a duck-typed bundle exposing ``word_filter``,
    ``frequency_service``, ``word_list_service``, ``wordset_service``,
    ``known_word_db``, and ``tagger`` (each optional except ``word_filter``).
    Read-only; a cancel returns the partial state examined so far (the worker
    discards it).
    """
    log_summary(logger, "Deck filter scan", deck=options.source_deck)
    note_ids = anki_service.find_notes(f'deck:"{_escape_anki_search(options.source_deck)}"')
    drops: Counter[str] = Counter()
    words: list[TokenizedWord] = []
    candidates: dict[int, _Candidate] = {}  # id(word) -> note pairing
    seen_expressions: set[str] = set()
    scanned = 0
    tagger = getattr(services, "tagger", None)
    script = get_profile(config_language(config)).script

    for chunk in _chunks(note_ids, _NOTES_CHUNK):
        if is_cancelled and is_cancelled():
            break
        for note in anki_service.notes_info(list(chunk)):
            fields = note.get("fields")
            note_id = note.get("noteId")
            model = note.get("modelName")
            if not isinstance(fields, dict) or not fields or not isinstance(note_id, int):
                continue
            scanned += 1
            raw = _field_value(fields, options.expression_field)
            if raw is None:
                # Picked field absent on this note's model: Anki convention
                # says the first field is the expression.
                first_field = next(iter(fields))
                raw = _field_value(fields, first_field) or ""
            expression = _strip_for_dedup(raw)
            if not expression:
                drops["no_expression"] += 1
                continue
            if not script.contains_target_script(expression):
                # Persisted plan key read by deck_filter_worker and pinned by
                # tests/unit/test_deck_filter.py — never renamed.
                drops["not_japanese"] += 1
                continue
            if expression in seen_expressions:
                drops["duplicate_in_source"] += 1
                continue
            seen_expressions.add(expression)
            word = _synthesize_word(expression, fields, options, tagger)
            words.append(word)
            candidates[id(word)] = _Candidate(
                note_id=note_id,
                model_name=model if isinstance(model, str) else "",
                fields={name: (_field_value(fields, name) or "") for name in fields},
                tags=tuple(t for t in note.get("tags", []) if isinstance(t, str)),
            )
        if progress:
            progress(min(scanned, len(note_ids)), len(note_ids))

    word_filter = services.word_filter

    # Known-words subtraction (also the against-the-collection duplicate gate).
    known = _collect_known_forms(anki_service, config, services, options.source_deck)
    before = len(words)
    words = word_filter.filter_unknown(words, known)
    drops["known"] += before - len(words)

    # Whitelist force-include: bypasses the coverage filters below.
    forced: list[TokenizedWord] = []
    word_list_service = getattr(services, "word_list_service", None)
    if config.use_whitelist and word_list_service is not None and word_list_service.is_available():
        forced, words = word_filter.partition_whitelisted(words, word_list_service)

    # Frequency band. Same gate as _phase2_filter: only a loaded NUMERIC
    # source can meaningfully apply a cutoff (a categorical-only source would
    # leave every rank None and the cutoff would wipe the whole deck).
    frequency_service = getattr(services, "frequency_service", None)
    if frequency_service is not None and frequency_service.is_available():
        _attach_frequency(words + forced, frequency_service)
    freq_low = config.min_frequency_rank
    freq_high = config.max_frequency_rank
    if (freq_low > 0 or freq_high > 0) and frequency_service is not None and frequency_service.has_numeric_source():
        survivors = word_filter.filter_by_frequency(
            words,
            freq_high,
            min_rank=freq_low,
            keep_unranked=config.frequency_keep_unranked,
        )
        kept_ids = {id(w) for w in survivors}
        for word in words:
            if id(word) not in kept_ids:
                drops["unranked" if word.frequency_rank is None else "frequency_band"] += 1
        words = survivors

    # Blacklist.
    if word_list_service is not None and word_list_service.is_available():
        before = len(words)
        words = word_filter.filter_by_word_lists(words, word_list_service)
        drops["blacklist"] += before - len(words)

    # Script type (for ja: hiragana-only / katakana-only forms).
    script_options = enabled_script_options(get_profile(config_language(config)).script, config)
    if script_options:
        before = len(words)
        words = word_filter.filter_by_script_type(
            words,
            config.exclude_hiragana_only_words,
            config.exclude_katakana_only_words,
            enabled_options=script_options,
        )
        drops["script_type"] += before - len(words)

    # Name wordsets (Issue #59).
    wordset_service = getattr(services, "wordset_service", None)
    if wordset_service is not None and wordset_service.is_available():
        before = len(words)
        words = word_filter.filter_by_wordsets(words, wordset_service)
        drops["name_wordset"] += before - len(words)

    kept: list[KeptNote] = []
    forced_ids = {id(word) for word in forced}
    for word in forced + words:
        candidate = candidates[id(word)]
        kept.append(
            KeptNote(
                note_id=candidate.note_id,
                model_name=candidate.model_name,
                fields=candidate.fields,
                tags=candidate.tags,
                expression=word.mined_form,
                reading=word.expression_reading,
                frequency_rank=word.frequency_rank,
                forced=id(word) in forced_ids,
            )
        )

    plan = DeckFilterPlan(
        options=options,
        kept=tuple(kept),
        drops=tuple((reason, drops[reason]) for reason in DROP_REASONS if drops[reason]),
        scanned=scanned,
        forced_count=len(forced),
        config_version=config.config_version,
    )
    log_summary(
        logger,
        "Deck filter scan done",
        deck=options.source_deck,
        scanned=scanned,
        kept=len(kept),
        forced=len(forced),
        **dict(plan.drops),
    )
    return plan


def apply_deck_filter(
    anki_service: AnkiService,
    plan: DeckFilterPlan,
    *,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> DeckFilterResult:
    """Create the target deck and copy the plan's kept notes into it.

    Copies scan-time values verbatim (no staleness recheck — a note edited
    between scan and apply copies as previewed). Every copy is a
    collection-wide duplicate by construction, so notes ship with
    ``allowDuplicate`` scoped to the target deck. Cancellation is honored
    between chunks; committed chunks stay. The vocab cache is invalidated in
    a ``finally`` — earlier chunks' notes exist even if a later one raises.
    """
    log_summary(
        logger,
        "Deck filter apply",
        deck=plan.options.target_deck,
        notes=len(plan.kept),
    )
    anki_service.ensure_deck(plan.options.target_deck)
    created = 0
    not_created = 0
    try:
        done = 0
        for chunk in _chunks(plan.kept, _ADD_CHUNK):
            if is_cancelled and is_cancelled():
                break
            notes = [
                {
                    "deckName": plan.options.target_deck,
                    "modelName": kept.model_name,
                    "fields": kept.fields,
                    "tags": [*kept.tags, DECKFILTER_TAG],
                    "options": {"allowDuplicate": True, "duplicateScope": "deck"},
                }
                for kept in chunk
            ]
            note_ids = anki_service.add_notes_raw(notes)
            created += sum(1 for nid in note_ids if nid is not None)
            not_created += sum(1 for nid in note_ids if nid is None)
            done += len(chunk)
            if progress:
                progress(done, len(plan.kept))
    finally:
        anki_service.invalidate_existing_vocabulary_cache()
    log_summary(
        logger,
        "Deck filter apply done",
        deck=plan.options.target_deck,
        created=created,
        not_created=not_created,
    )
    return DeckFilterResult(created=created, not_created=not_created)

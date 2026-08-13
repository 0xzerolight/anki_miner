"""Service for interacting with Anki via AnkiConnect."""

import logging
import re
from collections.abc import Callable, Iterator

import requests
from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError, SetupError
from anki_miner.interfaces import ProgressCallback
from anki_miner.models import AnkiWriteState, CardPayload
from anki_miner.services._ankiconnect import _expect_list, post_action, post_multi
from anki_miner.services.anki_media_store import AnkiMediaStore
from anki_miner.services.anki_note_builder import (
    OPTIONAL_FIELD_KEYS as _OPTIONAL_FIELD_KEYS,
)
from anki_miner.services.anki_note_builder import (
    REQUIRED_FIELD_KEYS as _REQUIRED_FIELD_KEYS,
)
from anki_miner.services.anki_note_builder import (
    _strip_for_dedup,
    build_note,
    configured_target_field_names,
    field_mapping_error,
    field_target_collision_message,
    missing_note_type_message,
)
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

# Matches any hiragana, katakana, or CJK ideograph (kanji)
_JAPANESE_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3400-\u4DBF]")

# updateNoteFields batching. Restyled glossary fields run 10-20 KB each, so a
# count-only chunk of 500 notes can hit ~7-8 MB in one `multi` body and trip the
# AnkiConnect oversized-body connection reset (mirrors anki_media_store's
# _MEDIA_BATCH_MAX_BYTES). Bound each POST by cumulative serialized field bytes
# AND note count, with a per-note fallback when a chunk still trips the reset.
_UPDATE_NOTES_CHUNK = 500
_UPDATE_NOTES_MAX_BYTES = 4 * 1024 * 1024


def _chunk_note_updates(
    updates: list[tuple[int, dict[str, str]]],
) -> Iterator[list[tuple[int, dict[str, str]]]]:
    """Yield ``(note_id, fields)`` sublists bounded by count and serialized-field bytes.

    Flushes the current chunk before adding a note that would push it past
    ``_UPDATE_NOTES_CHUNK`` notes or ``_UPDATE_NOTES_MAX_BYTES`` of cumulative
    field bytes. A single note larger than the byte budget still ships alone.
    Mirrors ``anki_media_store._chunk_media_actions``.
    """
    chunk: list[tuple[int, dict[str, str]]] = []
    chunk_bytes = 0
    for nid, fields in updates:
        entry_bytes = sum(len(v.encode("utf-8")) for v in fields.values())
        if chunk and (len(chunk) >= _UPDATE_NOTES_CHUNK or chunk_bytes + entry_bytes > _UPDATE_NOTES_MAX_BYTES):
            yield chunk
            chunk = []
            chunk_bytes = 0
        chunk.append((nid, fields))
        chunk_bytes += entry_bytes
    if chunk:
        yield chunk


# Yomitan's backend.js `_findDuplicates` classifies a note as a duplicate iff
# canAddNotesWithErrorDetail's per-note error string contains this exact literal
# (ext/js/background/backend.js:656, upstream e2ed450). A bare "duplicate"
# substring match — the previous approach — also swallowed genuine "…is a
# duplicate…"-free rejections, mislabeling bad field mappings as duplicates.
_DUPLICATE_ERROR_SUBSTRING = "cannot create note because it is a duplicate"

# AnkiConnect returns this top-level error for an action an older build lacks.
# Yomitan (partitionAddibleNotes) falls back to two diffed canAddNotes calls
# when canAddNotesWithErrorDetail is unavailable (backend.js:695).
_UNSUPPORTED_ACTION_SUBSTRING = "unsupported action"


def _escape_deck_name(deck: str) -> str:
    """Escape a deck name for use inside a quoted Anki search clause.

    Backslashes, quotes, and Anki's glob metacharacters (``*`` = any run,
    ``_`` = any single char, which Anki treats as wildcards even inside
    ``deck:"..."``) are escaped so a name like ``Core_2k`` matches literally.
    """
    return deck.replace("\\", "\\\\").replace('"', '\\"').replace("*", "\\*").replace("_", "\\_")


def is_transient_anki_transport_error(exc: BaseException) -> bool:
    """Whether *exc* is an AnkiConnect failure a later attempt could survive.

    Source-proven only, and deliberately narrow: the exception must be an
    :class:`AnkiConnectionError` chained from a real ``requests`` connection or
    timeout error — the two ``raise ... from e`` sites in
    ``_ankiconnect.post_action``/``post_multi``. Everything else keeps its
    ``__cause__`` empty or carries a deterministic one (an AnkiConnect-side
    error payload, an HTTP status, an unparseable body), and re-running it just
    fails the same way.

    Transience alone never authorizes a retry — the caller must also hold
    :attr:`AnkiWriteState.NO_NOTE_WRITE`, since a dropped connection *during*
    ``addNotes`` is both transient and unsafe to replay.
    """
    if not isinstance(exc, AnkiConnectionError):
        return False
    return isinstance(exc.__cause__, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))


class AnkiService:
    """Service for interacting with Anki via AnkiConnect (stateless service)."""

    # Field-mapping contract lives in anki_note_builder; aliased here because
    # callers and tests reference the keys via the service class.
    REQUIRED_FIELD_KEYS = _REQUIRED_FIELD_KEYS
    OPTIONAL_FIELD_KEYS = _OPTIONAL_FIELD_KEYS

    def __init__(self, config: AnkiMinerConfig):
        """Initialize the Anki service.

        Args:
            config: Configuration for Anki integration

        Raises:
            ValueError: If required field keys are missing from config
        """
        self.config = config
        # What this service can prove about note writes (D30). Only ever
        # escalated by create_cards_batch; reset per mining run by
        # EpisodeProcessor._run_pipeline, which is the sole run boundary.
        # A service that has not submitted anything has written nothing.
        self.anki_write_state: AnkiWriteState = AnkiWriteState.NO_NOTE_WRITE
        self.last_created_note_ids: list[int] = []
        # Positionally aligned mined forms for the confirmed IDs above. Unlike
        # ProcessingResult.mined_forms, this is not a known_words.db undo
        # receipt; Deck Builder consumes it to promote only cards Anki created.
        self.last_created_mined_forms: list[str] = []
        # Positionally aligned source lemmas for the same IDs. Deck Builder's
        # cross-episode dedup keys on corpus lemmas, which need not map
        # one-to-one to mined forms.
        self.last_created_lemmas: list[str] = []
        self._cancelled_check: Callable[[], bool] | None = None
        # Number of notes not created during the last create_cards_batch call.
        # Combines both sources:
        #   - notes the pre-add duplicate probe (_probe_duplicates) classified as
        #     duplicates before submission — the authoritative, per-note-attributed
        #     count (Anki flagged the same Expression as an existing card or another
        #     note in the batch)
        #   - any residual null slots in the addNotes result for notes the probe
        #     had cleared (a rare race — a duplicate landed between probe and add);
        #     folded in so a created-vs-submitted gap is never silent
        # Read by the pipeline to report skips.
        self.last_skipped_duplicates: int = 0
        # Number of media files (screenshots/audio) that could not be stored in
        # Anki during the last create_cards_batch call. Read by the pipeline to
        # warn the user when cards land with empty media fields. Mirrored from
        # the media store after each upload pass.
        self.last_media_store_failures: int = 0
        # Owns the storeMediaFile upload pipeline (chunking, per-file fallback)
        # and the per-run dict-media upload cache.
        self._media_store = AnkiMediaStore(config)
        # Session-scoped cache for get_existing_vocabulary. None means
        # unpopulated; subsequent calls return the cached set without
        # re-querying AnkiConnect. Call invalidate_existing_vocabulary_cache()
        # to force a refresh (e.g. after card creation or a manual sync).
        self._existing_vocab_cache: set[str] | None = None

        # Validate required field keys upfront
        missing = self.REQUIRED_FIELD_KEYS - set(config.anki_fields.keys())
        if missing:
            raise ValueError(f"Missing required anki_fields keys: {', '.join(sorted(missing))}")

    def get_note_type_fields(self, model_name: str | None = None) -> list[str]:
        """Get field names for a note type from AnkiConnect.

        Args:
            model_name: Note type name. Uses config value if None.

        Returns:
            List of field names, or empty list on error.
        """
        name = model_name or self.config.anki_note_type
        logger.debug("Anki get note type fields: note_type=%s", name)
        try:
            result = post_action(
                self.config.ankiconnect_url,
                "modelFieldNames",
                params={"modelName": name},
                timeout=15,
            )
        except AnkiConnectionError as exc:
            log_summary(
                logger,
                "Anki get note type fields done",
                level=logging.WARNING,
                note_type=name,
                fields=0,
                fallback=True,
                error_type=type(exc).__name__,
            )
            return []
        fields = list(result or [])
        logger.debug("Anki get note type fields done: fields=%d", len(fields))
        return fields

    def get_deck_names(self) -> list[str]:
        """Get all deck names from AnkiConnect.

        Returns:
            List of deck names, or empty list on error.
        """
        try:
            result = post_action(
                self.config.ankiconnect_url,
                "deckNames",
                timeout=15,
            )
        except AnkiConnectionError as exc:
            log_summary(
                logger,
                "Anki get deck names done",
                level=logging.WARNING,
                decks=0,
                fallback=True,
                error_type=type(exc).__name__,
            )
            return []
        decks = list(result or [])
        logger.debug("Anki get deck names done: decks=%d", len(decks))
        return decks

    def get_model_names(self) -> list[str]:
        """Get all note type (model) names from AnkiConnect.

        Mirrors :meth:`get_deck_names`: swallows :class:`AnkiConnectionError`
        and returns an empty list so a read-only probe never raises.

        Returns:
            List of note type names, or empty list on error.
        """
        try:
            result = post_action(
                self.config.ankiconnect_url,
                "modelNames",
                timeout=15,
            )
        except AnkiConnectionError as exc:
            log_summary(
                logger,
                "Anki get model names done",
                level=logging.WARNING,
                models=0,
                fallback=True,
                error_type=type(exc).__name__,
            )
            return []
        models = list(result or [])
        logger.debug("Anki get model names done: models=%d", len(models))
        return models

    def ensure_deck(self, deck_name: str) -> None:
        """Create the named deck in Anki via AnkiConnect.

        Idempotent: if the deck already exists, AnkiConnect returns its
        existing id without error — this method is safe to call unconditionally
        before routing cards to a deck.

        Raises:
            AnkiConnectionError: On connection failure or AnkiConnect error.
        """
        logger.debug("Anki ensure deck: deck=%s", deck_name)
        post_action(
            self.config.ankiconnect_url,
            "createDeck",
            params={"deck": deck_name},
            timeout=15,
        )
        logger.debug("Anki ensure deck done: deck=%s", deck_name)

    def note_type_names(self) -> list[str]:
        """Every note type in the collection (AnkiConnect ``modelNames``)."""
        names = _expect_list(
            post_action(self.config.ankiconnect_url, "modelNames", timeout=15) or [],
            "modelNames",
            elem_type=str,
        )
        logger.debug("Anki note type names done: note_types=%d", len(names))
        return names

    def note_type_field_names(self, note_type: str) -> set[str]:
        """Field names defined on ``note_type`` (AnkiConnect ``modelFieldNames``)."""
        return set(self.ordered_note_type_field_names(note_type))

    def ordered_note_type_field_names(self, note_type: str) -> list[str]:
        """Field names defined on ``note_type``, preserving Anki's order."""
        return self._ordered_note_type_field_names(note_type)

    def _ordered_note_type_field_names(self, note_type: str) -> list[str]:
        """Ordered field names defined on ``note_type``."""
        logger.debug("Anki note type field names: note_type=%s", note_type)
        fields = _expect_list(
            post_action(
                self.config.ankiconnect_url,
                "modelFieldNames",
                params={"modelName": note_type},
                timeout=15,
            )
            or [],
            "modelFieldNames",
            elem_type=str,
        )
        logger.debug("Anki note type field names done: fields=%d", len(fields))
        return fields

    def verify_card_target(self) -> None:
        """Validate note type, field mapping, and that the target deck exists.

        Pure check — creates nothing. Decks are no longer auto-created on the
        mining path: the Settings → Anki deck dropdown only offers decks that
        really exist, so a configured deck that is missing is a user-visible
        error rather than a silently-created stray deck. ``ensure_deck`` is
        still used by Deck Builder, which builds a genuinely new deck and calls
        it BEFORE its per-pair process_episode loop — that ordering is what
        makes this check pass there (see deck_builder_worker.py).

        Raises:
            SetupError: note type missing, field mapping invalid, or the
                configured deck absent from the collection.
            AnkiConnectionError: AnkiConnect unreachable or errors.
        """
        logger.debug(
            "Anki verify card target: deck=%s note_type=%s",
            self.config.anki_deck_name,
            self.config.anki_note_type,
        )
        models = self.note_type_names()
        if self.config.anki_note_type not in models:
            raise SetupError(missing_note_type_message(self.config.anki_note_type, models))

        targets = [target for target in self.config.anki_fields.values() if target]
        # The active card-type marker is written too (build_note stamps "x" into
        # it), so a marker sharing a target field would silently overwrite that
        # field after preflight passed. Inactive markers are never written and
        # may collide freely.
        if self.config.card_type:
            marker_target = self.config.card_type_marker_fields.get(self.config.card_type, "")
            if marker_target:
                targets.append(marker_target)
        collision_error = field_target_collision_message(self.config.anki_note_type, targets)
        if collision_error:
            raise SetupError(collision_error)

        ordered_actual = self.ordered_note_type_field_names(self.config.anki_note_type)
        actual = set(ordered_actual)
        required = configured_target_field_names(self.config)
        word_target = self.config.anki_fields["word"]
        mapping_error = field_mapping_error(
            self.config.anki_note_type,
            ordered_actual,
            required,
            word_target,
        )
        if mapping_error:
            raise SetupError(mapping_error)

        decks = post_action(self.config.ankiconnect_url, "deckNames", timeout=15) or []
        if self.config.anki_deck_name not in decks:
            available = ", ".join(decks[:5])
            more = "..." if len(decks) > 5 else ""
            raise SetupError(
                f"Deck '{self.config.anki_deck_name}' not found in Anki. "
                f"Available: {available}{more}. "
                f"Pick an existing deck in Settings → Anki, or create it in Anki first."
            )
        logger.debug(
            "Anki verify card target done: models=%d fields=%d configured=%d decks=%d",
            len(models),
            len(actual),
            len(required),
            len(decks),
        )

    def _build_vocab_query(self) -> str:
        """Build the findNotes query for known-words detection.

        Starts from the whole collection (``deck:*``) and negates each excluded
        deck (Issue #38). In Anki search, ``deck:"Name"`` matches the deck *and
        its subdecks*, so a parent exclusion covers nested decks automatically.
        Deck names are double-quoted; backslashes, quotes, and Anki's glob
        metacharacters (``*`` = any run, ``_`` = any single char, which Anki
        treats as wildcards even inside ``deck:"..."``) are escaped so a name
        like ``Core_2k`` matches literally instead of over-excluding ``CoreX2k``.
        """
        query = "deck:*"
        for deck in self.config.excluded_decks:
            query += f' -deck:"{_escape_deck_name(deck)}"'
        logger.debug("Anki vocab query: query=%s", query)
        return query

    def find_notes(self, query: str) -> list[int]:
        """Return note IDs matching an Anki search ``query`` (AnkiConnect ``findNotes``)."""
        logger.debug("Anki find notes: query=%s", query)
        note_ids = _expect_list(
            post_action(self.config.ankiconnect_url, "findNotes", params={"query": query}, timeout=30) or [],
            "findNotes",
            elem_type=int,
        )
        logger.debug("Anki find notes done: notes=%d", len(note_ids))
        return note_ids

    def notes_info(self, note_ids: list[int]) -> list[dict]:
        """Return per-note info dicts for ``note_ids`` (``notesInfo``); ``[]`` for empty input.

        Each dict carries ``noteId`` and a ``fields`` map ``{name: {"value": …}}``;
        a deleted note comes back as ``{}``.
        """
        logger.debug("Anki notes info: notes=%d", len(note_ids))
        if not note_ids:
            logger.debug("Anki notes info done: notes=%d", 0)
            return []
        notes = _expect_list(
            post_action(self.config.ankiconnect_url, "notesInfo", params={"notes": note_ids}, timeout=60) or [],
            "notesInfo",
            elem_type=dict,
        )
        logger.debug("Anki notes info done: notes=%d", len(notes))
        return notes

    def update_notes_fields(self, updates: list[tuple[int, dict[str, str]]]) -> list[int]:
        """Overwrite fields on many notes in one batch (``updateNoteFields`` via ``post_multi``).

        ``updates`` is ``[(note_id, {field_name: value})]``. Returns the ordered
        note IDs whose updates AnkiConnect confirmed. This writes note *content*
        (fields the app already fills at mining time), never note-type styling.
        Returns ``[]`` for empty input.
        """
        if not updates:
            return []
        updated_note_ids: list[int] = []
        for chunk in _chunk_note_updates(updates):
            updated_note_ids.extend(self._post_note_update_chunk(chunk))
        return updated_note_ids

    def _post_note_update_chunk(self, chunk: list[tuple[int, dict[str, str]]]) -> list[int]:
        """POST one ``updateNoteFields`` chunk via ``multi``; fall back per-note on transport failure.

        Returns ordered note IDs updated without an AnkiConnect error. A chunk
        oversized enough to trip the connection reset, or a malformed result
        cardinality, surfaces as an ``AnkiConnectionError``; we then retry each
        note in its own tiny POST (like
        ``AnkiMediaStore._store_media_files_individually``) so one bad chunk
        doesn't abort the whole restyle.
        """
        actions = [
            {"action": "updateNoteFields", "version": 6, "params": {"note": {"id": nid, "fields": fields}}}
            for nid, fields in chunk
        ]
        try:
            results = _expect_list(
                post_multi(self.config.ankiconnect_url, actions, timeout=60),
                "multi",
                len(actions),
            )
        except AnkiConnectionError as e:
            logger.warning(
                "updateNoteFields multi POST failed (%s); retrying %d note(s) individually",
                e,
                len(actions),
            )
            return self._update_notes_individually(chunk)
        return [
            nid
            for (nid, _fields), sub in zip(chunk, results, strict=True)
            if not (isinstance(sub, dict) and sub.get("error"))
        ]

    def _update_notes_individually(self, chunk: list[tuple[int, dict[str, str]]]) -> list[int]:
        """Per-note ``updateNoteFields`` fallback (tiny bodies) for a failed-multi chunk."""
        updated_note_ids: list[int] = []
        for nid, fields in chunk:
            try:
                post_action(
                    self.config.ankiconnect_url,
                    "updateNoteFields",
                    params={"note": {"id": nid, "fields": fields}},
                    timeout=60,
                )
                updated_note_ids.append(nid)
            except AnkiConnectionError as e:
                logger.warning("Failed to update note %s individually: %s", nid, e)
        return updated_note_ids

    def add_tags(self, note_ids: list[int], tags: str) -> None:
        """Add ``tags`` to notes (AnkiConnect ``addTags``); no-op for empty input.

        Used by the Card Backfill tool to mark touched notes so users can find
        (and revert) them in Anki's browser. ``addTags`` returns null, so there
        is nothing to parse; chunked to keep request bodies small.
        """
        logger.debug("Anki add tags: notes=%d tag=%s", len(note_ids), tags)
        for start in range(0, len(note_ids), 500):
            chunk = note_ids[start : start + 500]
            post_action(
                self.config.ankiconnect_url,
                "addTags",
                params={"notes": chunk, "tags": tags},
                timeout=60,
            )
        logger.debug("Anki add tags done: notes=%d", len(note_ids))

    def get_existing_vocabulary(self, *, allow_degraded: bool = True) -> set[str]:
        """Get all Japanese vocabulary words already in Anki.

        Queries the collection (minus any ``config.excluded_decks``; see
        :meth:`_build_vocab_query`) and extracts the first field from each note,
        which by Anki convention is always the expression/word being studied.
        Only words containing Japanese characters are included.

        Returns:
            Set of Expression (first-field) values already in the
            collection, dedup-normalized (HTML/media-stripped, NFC) — i.e.
            ``mined_form`` strings, not lemmas. Returns an
            empty set as a graceful-degradation fallback when
            ``allow_degraded`` is true and AnkiConnect
            responds but the call fails for a recoverable, non-connection
            transport reason (e.g. a ``Timeout`` or a JSON decode
            ``ValueError``) — a warning is logged and filtering is
            effectively disabled for the run.

        Raises:
            AnkiConnectionError: If a connection to AnkiConnect cannot be
                established, or if AnkiConnect itself returns an error
                payload for ``findNotes`` / ``notesInfo``.
        """
        if self._existing_vocab_cache is not None:
            logger.debug("get_existing_vocabulary: returning %d words from cache", len(self._existing_vocab_cache))
            return self._existing_vocab_cache

        try:
            self._existing_vocab_cache = self._collect_first_field_forms(self._build_vocab_query())
            return self._existing_vocab_cache

        except AnkiConnectionError as e:
            # `post_action` translates `ConnectionError` (Anki down) and
            # AnkiConnect-side error payloads to `AnkiConnectionError` —
            # both must propagate so the GUI can surface a hard failure.
            # Other transport failures (`Timeout`, JSON parse) are wrapped
            # with `__cause__` set to a `RequestException`/`ValueError`;
            # those degrade to an empty set + warning.
            cause = e.__cause__
            if cause is None or isinstance(cause, requests.exceptions.ConnectionError):
                raise
            if not allow_degraded:
                raise
            logger.warning("Failed to fetch existing vocabulary (filtering disabled): %s", e)
            return set()

    def _collect_first_field_forms(self, query: str) -> set[str]:
        """Run ``query`` and return the dedup-normalized Japanese first fields.

        The findNotes → notesInfo → first-field scan shared by
        :meth:`get_existing_vocabulary` and
        :meth:`get_vocabulary_excluding_deck`. Raises ``AnkiConnectionError``
        on transport failure; degradation policy belongs to the callers.
        """
        note_ids = _expect_list(
            post_action(
                self.config.ankiconnect_url,
                "findNotes",
                params={"query": query},
                timeout=30,
            )
            or [],
            "findNotes",
            elem_type=int,
        )

        if not note_ids:
            logger.warning(
                "No notes found in Anki collection. "
                "If you have cards in Anki, check that AnkiConnect can access them.",
            )
            return set()

        # Get note info in batches to avoid timeouts on large collections.
        existing_words: set[str] = set()
        batch_size = 1000

        for i in range(0, len(note_ids), batch_size):
            batch = note_ids[i : i + batch_size]
            notes = _expect_list(
                post_action(
                    self.config.ankiconnect_url,
                    "notesInfo",
                    params={"notes": batch},
                    timeout=30,
                )
                or [],
                "notesInfo",
                elem_type=dict,
            )

            for note in notes:
                # A deleted note comes back as `{}`, and a malformed row may
                # carry a non-dict `fields`; both are treated as absent.
                fields = note.get("fields")
                if not isinstance(fields, dict) or not fields:
                    continue
                # First field is always the expression/word in Anki
                # convention. Normalize it the same way Anki dedups (strip
                # HTML/media, unescape, NFC) so a markup-wrapped Expression
                # matches the plain `mined_form` the filter compares against
                # — otherwise the word slips the filter and AnkiConnect
                # rejects it as a duplicate at addNotes time.
                first_field = next(iter(fields))
                field_info = fields[first_field]
                if not isinstance(field_info, dict):
                    # Malformed field entry (not a {value, order} object).
                    continue
                word = _strip_for_dedup(field_info.get("value", ""))
                if word and _JAPANESE_RE.search(word):
                    existing_words.add(word)

        return existing_words

    def get_vocabulary_excluding_deck(self, deck: str) -> set[str]:
        """Existing-vocabulary scan that additionally negates ``deck``.

        Used by the Deck Filter tool: the source deck's own expressions must
        not count as "known", otherwise every note in the deck being filtered
        would be dropped against itself. Appending the negation is idempotent
        when ``deck`` is already in ``config.excluded_decks``.

        Never reads or writes the session vocab cache — the cache holds the
        answer for :meth:`_build_vocab_query`'s shape, and this query's answer
        must not leak into callers of :meth:`get_existing_vocabulary`. Raises
        ``AnkiConnectionError`` on any failure: a degraded empty set would
        silently classify the whole source deck as unknown-but-known-elsewhere
        and produce a wrong plan.
        """
        query = self._build_vocab_query() + f' -deck:"{_escape_deck_name(deck)}"'
        return self._collect_first_field_forms(query)

    def invalidate_existing_vocabulary_cache(self) -> None:
        """Invalidate the session-scoped vocabulary cache.

        The next call to ``get_existing_vocabulary`` will re-query AnkiConnect.
        Call this after creating new cards or after a manual Anki sync so that
        the filter reflects the updated collection.
        """
        self._existing_vocab_cache = None

    @property
    def _dict_media_uploaded(self) -> set[str]:
        """Dict-media srcs already shipped this run (owned by the media store)."""
        return self._media_store._dict_media_uploaded

    def _upload_dict_media_batch(self, word_data_list: list["CardPayload"]) -> None:
        """Batch-upload all dict-media assets referenced across the whole card batch.

        Delegates to :meth:`AnkiMediaStore.upload_dict_media`: srcs are cached
        only after a confirmed successful store (missing-on-disk srcs are
        cached deliberately so they are not retried on every card); a failed
        upload stays uncached so the next batch retries it.
        """
        self._media_store.upload_dict_media(word_data_list)

    def set_cancelled_check(self, cancelled: Callable[[], bool] | None) -> None:
        """Install the live Phase-5 cancellation predicate for one call."""
        self._cancelled_check = cancelled

    def create_cards_batch(
        self,
        word_data_list: list[CardPayload],
        progress_callback: ProgressCallback | None = None,
    ) -> list[int]:
        """Create multiple Anki cards in batches.

        Args:
            word_data_list: List of CardPayload objects to submit
            progress_callback: Optional callback for progress reporting

        Returns:
            Ordered note IDs successfully created and confirmed by AnkiConnect.
        """
        log_summary(
            logger,
            "Anki create cards",
            cards=len(word_data_list),
            deck=self.config.anki_deck_name,
            note_type=self.config.anki_note_type,
        )
        if not word_data_list:
            self.last_created_note_ids = []
            self.last_created_mined_forms = []
            self.last_created_lemmas = []
            self.last_skipped_duplicates = 0
            self.last_media_store_failures = 0
            log_summary(
                logger,
                "Anki create cards done",
                cards=0,
                created=0,
                not_created=0,
                media_failed=0,
                duplicates=0,
                bold_used=0,
                bold_fallback=0,
            )
            return []

        self.last_created_note_ids = []
        self.last_created_mined_forms = []
        self.last_created_lemmas = []
        self.last_skipped_duplicates = 0
        self.last_media_store_failures = 0
        skipped_duplicates = 0
        probed_duplicates = 0
        all_created_ids: list[int] = []

        if progress_callback:
            progress_callback.on_start(
                len(word_data_list),
                QCoreApplication.translate("AnkiService", "Creating Anki cards"),
            )

        excluded_deck_admission = bool(self.config.excluded_decks and not self.config.allow_duplicate_cards)
        if excluded_deck_admission:
            # The normal Phase-2 admission query deliberately excludes these
            # decks. Reuse that same answer here, then allow admitted notes past
            # Anki's collection-wide duplicate rule. Keep a local seen set so
            # one run still submits a given first field at most once.
            # This answer authorizes bypassing Anki's collection-wide duplicate
            # rule. An uncertain answer must fail closed, unlike Phase 2's
            # best-effort filtering preview.
            existing = self.get_existing_vocabulary(allow_degraded=False)
            seen: set[str] = set()
            candidate_payloads: list[CardPayload] = []
            for item in word_data_list:
                note = build_note(item, self.config, set()).note
                fields = note.get("fields") or {}
                first_value = next(iter(fields.values()), "")
                key = _strip_for_dedup(first_value if isinstance(first_value, str) else "")
                duplicate = bool(key and (key in existing or key in seen))
                if duplicate:
                    skipped_duplicates += 1
                else:
                    candidate_payloads.append(item)
                if key:
                    seen.add(key)
        else:
            candidate_payloads = list(word_data_list)
        probed_duplicates = skipped_duplicates

        # Create surviving notes in batches. AnkiConnect accepts arbitrary array
        # sizes; 100 cuts round-trips ~2x vs 50 with no observed errors on a
        # representative deck. Larger sizes (200+) show diminishing returns
        # because note construction time inside Anki dominates over HTTP.
        batch_size = 100
        total_created = 0
        # mined_forms of cards actually created (non-null id) this run, for the
        # incremental cache merge in the finally. Only created words are merged —
        # see the rationale there (F10).
        created_forms: list[str] = []
        created_lemmas: list[str] = []
        # Diagnostic counters for the bold path (Issue #20). Surface whether
        # the precomputed bolded strings actually made it to the note body,
        # so users who enable the option but see no bold can tell from the
        # log whether the parse populated the fields.
        bold_used = 0
        bold_fallback = 0
        media_store_failures = 0
        cancelled_between_batches = False

        # Persist progress even if a later batch raises. Earlier batches'
        # cards already exist in Anki; on a mid-run failure we must still
        # record their note IDs (so Undo works) and invalidate the now-stale
        # vocab cache before the error propagates — otherwise those cards are
        # orphaned with no record. The `finally` runs on success AND failure.
        try:
            for i in range(0, len(candidate_payloads), batch_size):
                if self._cancelled_check is not None and self._cancelled_check():
                    cancelled_between_batches = True
                    break

                candidate_batch = candidate_payloads[i : i + batch_size]
                # Probe each chunk only after the prior chunk's addNotes response.
                # A repeated first field crossing the 100-note boundary then sees
                # the first note in Anki and is rejected before any of its media is
                # uploaded. Excluded-deck admission deliberately permits collection
                # duplicates, but still validates every locally admitted note with
                # duplicates allowed so bad fields fail before media side effects.
                probe_notes = [build_note(item, self.config, set()).note for item in candidate_batch]
                if excluded_deck_admission:
                    self._validate_notes_addible(probe_notes)
                    batch = candidate_batch
                else:
                    is_duplicate = self._probe_duplicates(probe_notes)
                    batch = [
                        item for item, duplicate in zip(candidate_batch, is_duplicate, strict=True) if not duplicate
                    ]
                    batch_duplicates = sum(is_duplicate)
                    skipped_duplicates += batch_duplicates
                    probed_duplicates += batch_duplicates

                if not batch:
                    continue

                # Upload only this confirmed-to-be-addable batch. A Stop after
                # it commits prevents every later batch's media and notes.
                stored_files = self._store_media_files_batch(batch)
                media_store_failures += self.last_media_store_failures
                self._upload_dict_media_batch(batch)

                # Build notes array for this batch (field mapping lives in
                # anki_note_builder).
                notes = []
                for item in batch:
                    built = build_note(item, self.config, stored_files)
                    if built.used_precomputed_bold:
                        bold_used += 1
                    if built.used_bold_fallback:
                        bold_fallback += 1
                    note = built.note
                    if excluded_deck_admission:
                        note["options"] = {"allowDuplicate": True}
                    notes.append(note)

                submit_notes = notes
                submit_payloads = batch

                # Submit only the non-duplicates. `post_action` raises
                # `AnkiConnectionError` for connection failures, transport errors,
                # and AnkiConnect-side error payloads. `_expect_list` enforces the
                # addNotes contract: a list of exactly len(submit_notes) slots,
                # each an id (int) or null (None); length alignment is load-bearing
                # for the positional zip below.
                if submit_notes:
                    # Note-write provenance (D30). From the moment the request
                    # leaves this process until a VALIDATED response comes back,
                    # the honest answer is "we cannot tell": a dropped
                    # connection or an unreadable body may well have created the
                    # notes. Anything that escapes between these two lines
                    # therefore leaves NOTE_WRITE_UNCERTAIN behind, which blocks
                    # automatic retry. Only the validated response downgrades it
                    # again — and only back to what held BEFORE this batch, so a
                    # later all-duplicate batch cannot erase an earlier batch's
                    # confirmed write.
                    state_before_request = self.anki_write_state
                    self.anki_write_state = AnkiWriteState.NOTE_WRITE_UNCERTAIN
                    logger.debug("Anki write state: %s", self.anki_write_state.value)
                    note_ids = _expect_list(
                        post_action(
                            self.config.ankiconnect_url,
                            "addNotes",
                            params={"notes": submit_notes},
                            timeout=60,
                        ),
                        "addNotes",
                        len(submit_notes),
                        (int, type(None)),
                    )
                    if any(nid is not None for nid in note_ids):
                        self.anki_write_state = AnkiWriteState.NOTE_WRITE_CONFIRMED
                    else:
                        self.anki_write_state = state_before_request
                    logger.debug("Anki write state: %s", self.anki_write_state.value)
                else:
                    note_ids = []

                # Count successful creations (non-null IDs). A null slot here is a
                # note the probe had cleared that addNotes still didn't create — a
                # rare race (a duplicate landed between probe and add). Fold those
                # into the not-created count so the gap is never silent.
                batch_created = sum(1 for nid in note_ids if nid is not None)
                skipped_duplicates += len(submit_notes) - batch_created
                total_created += batch_created
                all_created_ids.extend(nid for nid in note_ids if nid is not None)
                # note_ids align positionally with `submit_payloads` (both derive
                # from the same probe partition and addNotes is length-checked by
                # _expect_list), so only the submitted, created words are merged.
                created_forms.extend(
                    item.word.mined_form for item, nid in zip(submit_payloads, note_ids, strict=True) if nid is not None
                )
                created_lemmas.extend(
                    item.word.lemma for item, nid in zip(submit_payloads, note_ids, strict=True) if nid is not None
                )

                if progress_callback:
                    # Report the CUMULATIVE run total, never per-chunk figures:
                    # a per-batch "{batch_created}/{len(batch)}" reads as
                    # "100/100 cards done" on every full chunk regardless of
                    # the real run total (the reported Issue: misleading
                    # "Cards created: 100/100").
                    progress_callback.on_progress(
                        min(i + len(candidate_batch) + skipped_duplicates, len(word_data_list)),
                        tr_format(
                            QCoreApplication.translate("AnkiService", "Cards created: %1/%2"),
                            total_created,
                            len(word_data_list),
                        ),
                    )
        finally:
            # Record whatever batches completed (all of them on success, the
            # earlier ones on a mid-run failure). Runs before the exception
            # re-raises.
            self.last_created_note_ids = all_created_ids
            self.last_created_mined_forms = created_forms
            self.last_created_lemmas = created_lemmas
            self.last_skipped_duplicates = skipped_duplicates
            self.last_media_store_failures = media_store_failures
            # Incremental merge: if the cache is already populated, union the
            # mined_forms of cards actually CREATED this run into it so subsequent
            # episodes (within the same batch run or the same manual-pair session)
            # get a cheap cache hit instead of a full collection re-scan.
            # Only created words are merged — NOT every attempted word: a null
            # addNotes slot is usually a duplicate (already in the collection, and
            # thus already in the cache from the initial scan), but it can also be
            # a non-duplicate silent rejection (bad model/field) for a word that is
            # NOT in the collection. Merging those would wrongly mark them "known"
            # and filter them out of later batch items. When the cache is None
            # (not yet populated), leave it None so the next call scans normally.
            if self._existing_vocab_cache is not None:
                for form in created_forms:
                    key = _strip_for_dedup(form)
                    if key and _JAPANESE_RE.search(key):
                        self._existing_vocab_cache.add(key)

        if progress_callback and not cancelled_between_batches:
            progress_callback.on_complete()
        if skipped_duplicates > 0:
            logger.info(
                "%d note(s) were not created (likely already in your collection).",
                skipped_duplicates,
            )
        if self.config.bold_target_in_sentence and word_data_list:
            logger.info(
                "bold_target_in_sentence=on: precomputed bold used on %d/%d cards (escape fallback: %d)",
                bold_used,
                len(word_data_list),
                bold_fallback,
            )
        log_summary(
            logger,
            "Anki create cards done",
            cards=len(word_data_list),
            created=total_created,
            not_created=skipped_duplicates,
            media_failed=self.last_media_store_failures,
            duplicates=probed_duplicates,
            bold_used=bold_used,
            bold_fallback=bold_fallback,
        )
        return list(all_created_ids)

    def add_notes_raw(self, notes: list[dict]) -> list[int | None]:
        """POST caller-built note dicts via ``addNotes`` in chunks of 100.

        Unlike :meth:`create_cards_batch`, the caller owns every part of the
        payload — ``deckName``, ``modelName``, ``fields``, ``tags``,
        ``options`` (the Deck Filter tool copies scan-time notes verbatim into
        a new deck). No duplicate probe and no media upload happen here; a
        same-collection copy's ``<img>``/``[sound:]`` refs already resolve.

        Returns ids positionally aligned with ``notes`` (``None`` = not
        created). Write provenance mirrors ``create_cards_batch``: the state
        is ``NOTE_WRITE_UNCERTAIN`` while a chunk is in flight (and stays so
        if the request escapes), ``NOTE_WRITE_CONFIRMED`` once any id comes
        back non-null, else restored to what held before the chunk.
        """
        log_summary(logger, "Anki add raw notes", notes=len(notes))
        results: list[int | None] = []
        batch_size = 100
        for i in range(0, len(notes), batch_size):
            chunk = notes[i : i + batch_size]
            state_before_request = self.anki_write_state
            self.anki_write_state = AnkiWriteState.NOTE_WRITE_UNCERTAIN
            logger.debug("Anki write state: %s", self.anki_write_state.value)
            note_ids = _expect_list(
                post_action(
                    self.config.ankiconnect_url,
                    "addNotes",
                    params={"notes": chunk},
                    timeout=60,
                ),
                "addNotes",
                len(chunk),
                (int, type(None)),
            )
            if any(nid is not None for nid in note_ids):
                self.anki_write_state = AnkiWriteState.NOTE_WRITE_CONFIRMED
            else:
                self.anki_write_state = state_before_request
            logger.debug("Anki write state: %s", self.anki_write_state.value)
            results.extend(note_ids)
        log_summary(
            logger,
            "Anki add raw notes done",
            notes=len(notes),
            created=sum(1 for nid in results if nid is not None),
        )
        return results

    @staticmethod
    def _strip_note_to_first_field(note: dict) -> dict:
        """Return a shallow clone of ``note`` keeping only its first field.

        Ported from Yomitan ``Backend._stripNotesArray``
        (``ext/js/background/backend.js``, upstream e2ed450). Anki dedups on the
        first field only, so shipping the rest — definition/glossary fields can
        carry megabytes of rendered HTML — just to ask "is this a duplicate?"
        wastes bandwidth and AnkiConnect time. ``verify_card_target`` requires
        the word mapping to target the model's first field and rejects mapping
        collisions; ``build_note`` emits that mined-form field first.
        """
        stripped = dict(note)
        fields = note.get("fields") or {}
        if fields:
            first_key = next(iter(fields))
            stripped["fields"] = {first_key: fields[first_key]}
        else:
            stripped["fields"] = {}
        return stripped

    def _probe_duplicates(self, notes: list[dict]) -> list[bool]:
        """Return, per note, whether AnkiConnect would reject it as a duplicate.

        Ported from Yomitan ``Backend.partitionAddibleNotes`` /
        ``_findDuplicates`` (``ext/js/background/backend.js``) and
        ``AnkiConnect.canAddNotesWithErrorDetail``
        (``ext/js/comm/anki-connect.js``), upstream e2ed450. Sends first-field-only
        clones with ``allowDuplicate: False`` (merged over each note's own
        options, e.g. ``duplicateScope``) so ``canAdd`` reflects duplicate status,
        then classifies a note as a duplicate iff its per-note error contains the
        literal duplicate substring. Any OTHER non-null error (an empty first
        field, a bad field mapping) is surfaced as an :class:`AnkiConnectionError`
        rather than silently miscounted as a duplicate — the core fix over the old
        null-slot inference. On an older AnkiConnect without
        ``canAddNotesWithErrorDetail`` (top-level "unsupported action"), falls back
        to two diffed ``canAddNotes`` calls.

        Raises:
            AnkiConnectionError: connection/transport failure, a malformed
                response, or a per-note non-duplicate rejection.
        """
        logger.debug("Anki duplicate probe: notes=%d", len(notes))
        if not notes:
            logger.debug("Anki duplicate probe done: duplicates=%d", 0)
            return []

        stripped = [self._strip_note_to_first_field(note) for note in notes]
        # Flip allowDuplicate off (Yomitan notesNoDuplicatesAllowed) so a
        # duplicate reports canAdd=false with the duplicate error; keep the note's
        # own options otherwise. Normal-path notes carry no options, so this is
        # AnkiConnect's default anyway; Deck Builder notes keep duplicateScope.
        no_dup = [{**note, "options": {**note.get("options", {}), "allowDuplicate": False}} for note in stripped]

        try:
            result = _expect_list(
                post_action(
                    self.config.ankiconnect_url,
                    "canAddNotesWithErrorDetail",
                    params={"notes": no_dup},
                    timeout=60,
                ),
                "canAddNotesWithErrorDetail",
                len(notes),
                dict,
            )
        except AnkiConnectionError as e:
            if _UNSUPPORTED_ACTION_SUBSTRING in str(e).lower():
                logger.debug(
                    "Anki duplicate probe fallback: reason=unsupported_action error_type=%s",
                    type(e).__name__,
                )
                fallback_result = self._probe_duplicates_fallback(stripped, no_dup)
                logger.debug("Anki duplicate probe done: duplicates=%d", sum(fallback_result))
                return fallback_result
            raise

        is_duplicate: list[bool] = []
        for i, item in enumerate(result):
            error = item.get("error")
            if not isinstance(error, str):
                # canAdd=true (error null): addable, not a duplicate.
                is_duplicate.append(False)
            elif _DUPLICATE_ERROR_SUBSTRING in error:
                is_duplicate.append(True)
            else:
                # A genuine, non-duplicate rejection: surface it instead of
                # mislabeling it a duplicate and silently dropping the card.
                logger.debug(
                    "Anki duplicate probe rejected: index=%d can_add=%s error=%s",
                    i,
                    item.get("canAdd"),
                    error,
                )
                raise AnkiConnectionError(f"AnkiConnect rejected note {i} (not a duplicate): {error}")
        logger.debug("Anki duplicate probe done: duplicates=%d", sum(is_duplicate))
        return is_duplicate

    def _validate_notes_addible(self, notes: list[dict]) -> None:
        """Raise if any first-field-only note is invalid with duplicates allowed."""
        if not notes:
            return

        stripped = [self._strip_note_to_first_field(note) for note in notes]
        dup_allowed = [{**note, "options": {**note.get("options", {}), "allowDuplicate": True}} for note in stripped]
        try:
            result = _expect_list(
                post_action(
                    self.config.ankiconnect_url,
                    "canAddNotesWithErrorDetail",
                    params={"notes": dup_allowed},
                    timeout=60,
                ),
                "canAddNotesWithErrorDetail",
                len(notes),
                dict,
            )
        except AnkiConnectionError as e:
            if _UNSUPPORTED_ACTION_SUBSTRING not in str(e).lower():
                raise
            addible = _expect_list(
                post_action(
                    self.config.ankiconnect_url,
                    "canAddNotes",
                    params={"notes": dup_allowed},
                    timeout=60,
                ),
                "canAddNotes",
                len(notes),
                bool,
            )
            for index, can_add in enumerate(addible):
                if not can_add:
                    raise AnkiConnectionError(
                        f"AnkiConnect rejected note {index} even with duplicates allowed"
                    ) from None
            return

        for index, item in enumerate(result):
            error = item.get("error")
            if error is not None or item.get("canAdd") is not True:
                detail = error if isinstance(error, str) and error else "note is not addable"
                raise AnkiConnectionError(f"AnkiConnect rejected note {index}: {detail}")

    def _probe_duplicates_fallback(self, stripped: list[dict], no_dup: list[dict]) -> list[bool]:
        """Classify duplicates via two diffed ``canAddNotes`` calls.

        Ported from Yomitan ``Backend._findDuplicatesFallback``
        (``ext/js/background/backend.js``, upstream e2ed450), used when the newer
        ``canAddNotesWithErrorDetail`` is unavailable. A note is a duplicate iff it
        is addable with duplicates allowed but not with duplicates disallowed.
        ``stripped`` carries each note's own options, which for the normal mining
        path omit ``allowDuplicate`` — so, unlike upstream (whose notes default it
        on), we force ``allowDuplicate: True`` on the duplicates-allowed arm to
        make the diff meaningful.
        """
        logger.debug("Anki duplicate fallback probe: notes=%d", len(stripped))
        dup_allowed = [{**note, "options": {**note.get("options", {}), "allowDuplicate": True}} for note in stripped]
        with_dup = _expect_list(
            post_action(
                self.config.ankiconnect_url,
                "canAddNotes",
                params={"notes": dup_allowed},
                timeout=60,
            ),
            "canAddNotes",
            len(stripped),
            bool,
        )
        without_dup = _expect_list(
            post_action(
                self.config.ankiconnect_url,
                "canAddNotes",
                params={"notes": no_dup},
                timeout=60,
            ),
            "canAddNotes",
            len(no_dup),
            bool,
        )
        is_duplicate = [w != wo for w, wo in zip(with_dup, without_dup, strict=True)]
        logger.debug("Anki duplicate fallback probe done: duplicates=%d", sum(is_duplicate))
        return is_duplicate

    def _store_media_files_batch(
        self,
        word_data_list: list[CardPayload],
    ) -> set[str]:
        """Store card media (screenshots/audio) via the media store.

        Delegates to :meth:`AnkiMediaStore.store_batch` (chunked ``multi``
        POSTs with a per-file fallback) and mirrors its failure count onto
        ``self.last_media_store_failures`` so callers can surface it to the
        user instead of silently creating cards with empty media fields.

        Args:
            word_data_list: List of CardPayload objects whose media should be uploaded

        Returns:
            Set of filenames that were successfully stored
        """
        logger.debug("Anki store media files: cards=%d", len(word_data_list))
        stored = self._media_store.store_batch(word_data_list)
        self.last_media_store_failures = self._media_store.last_store_failures
        logger.debug(
            "Anki store media files done: files=%d failed=%d",
            len(stored),
            self.last_media_store_failures,
        )
        return stored

    def delete_notes(self, note_ids: list[int]) -> int:
        """Delete notes from Anki by their IDs.

        Note: AnkiConnect's deleteNotes action does not report per-note
        success/failure, so this returns the number of notes *requested*
        for deletion, not a verified count.

        Args:
            note_ids: List of Anki note IDs to delete

        Returns:
            Number of notes requested for deletion (assumes all succeeded
            if no error was raised)

        Raises:
            AnkiConnectionError: On any AnkiConnect failure — connection
                refused, transport error, JSON parse failure, or an error
                payload in the ``deleteNotes`` response.
        """
        log_summary(logger, "Anki delete notes", notes=len(note_ids))
        if not note_ids:
            log_summary(logger, "Anki delete notes done", notes=0)
            return 0

        post_action(
            self.config.ankiconnect_url,
            "deleteNotes",
            params={"notes": note_ids},
            timeout=30,
        )
        self.invalidate_existing_vocabulary_cache()
        deleted = len(note_ids)
        log_summary(logger, "Anki delete notes done", notes=deleted)
        return deleted

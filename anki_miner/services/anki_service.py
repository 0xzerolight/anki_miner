"""Service for interacting with Anki via AnkiConnect."""

import logging
import re
from typing import Any

import requests

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError, SetupError
from anki_miner.interfaces import ProgressCallback
from anki_miner.models import CardPayload
from anki_miner.services._ankiconnect import _expect_list, post_action, post_multi
from anki_miner.services.anki_media_store import AnkiMediaStore
from anki_miner.services.anki_note_builder import (
    OPTIONAL_FIELD_KEYS as _OPTIONAL_FIELD_KEYS,
)
from anki_miner.services.anki_note_builder import (
    REQUIRED_FIELD_KEYS as _REQUIRED_FIELD_KEYS,
)
from anki_miner.services.anki_note_builder import (
    _get_root_deck_name,
    _strip_for_dedup,
    build_note,
)

logger = logging.getLogger(__name__)

# Matches any hiragana, katakana, or CJK ideograph (kanji)
_JAPANESE_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3400-\u4DBF]")


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
        self.last_created_note_ids: list[int] = []
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
        # Number of existing notes coalesce-updated during the last
        # create_cards_batch call (config.duplicate_behavior == "update"): a
        # probe-flagged duplicate whose existing note had >=1 empty mapped field
        # this run could fill. Stays 0 in the default "skip" mode. Read by the
        # pipeline to report the "N cards updated" summary. These are NOT new
        # notes: their IDs never enter last_created_note_ids, so Undo — which
        # deletes last_created_note_ids — never touches a pre-existing user note.
        self.last_updated_notes: int = 0
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
        try:
            result = post_action(
                self.config.ankiconnect_url,
                "modelFieldNames",
                params={"modelName": name},
                timeout=15,
            )
        except AnkiConnectionError:
            return []
        return list(result or [])

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
        except AnkiConnectionError:
            return []
        return list(result or [])

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
        except AnkiConnectionError:
            return []
        return list(result or [])

    def get_model_styling(self, model_name: str | None = None) -> str:
        """Return the note type's current card CSS via AnkiConnect ``modelStyling``.

        Unlike the read-only fetch helpers that swallow errors and return a
        neutral empty value, this lets :class:`AnkiConnectionError` propagate so
        the caller (the Card Styling worker) can report a hard failure — Anki
        down, or the configured note type not existing — honestly instead of
        silently writing styling against a missing model.

        Args:
            model_name: Note type name. Uses ``config.anki_note_type`` if None.

        Returns:
            The note type's CSS, or an empty string if AnkiConnect responds but
            reports no styling for the model.

        Raises:
            AnkiConnectionError: If AnkiConnect cannot be reached or returns an
                error payload (e.g. the model was not found).
        """
        name = model_name or self.config.anki_note_type
        result = post_action(
            self.config.ankiconnect_url,
            "modelStyling",
            params={"modelName": name},
            timeout=15,
        )
        if isinstance(result, dict):
            return str(result.get("css", "") or "")
        return ""

    def update_model_styling(self, css: str, model_name: str | None = None) -> None:
        """Push ``css`` into the note type's card styling via ``updateModelStyling``.

        The CSS is sent verbatim as an opaque JSON string value — it must NOT be
        HTML-escaped. Errors propagate (see :meth:`get_model_styling`).

        Args:
            css: The full styling string to write to the note type.
            model_name: Note type name. Uses ``config.anki_note_type`` if None.

        Raises:
            AnkiConnectionError: If AnkiConnect cannot be reached or returns an
                error payload (e.g. the model was not found).
        """
        name = model_name or self.config.anki_note_type
        post_action(
            self.config.ankiconnect_url,
            "updateModelStyling",
            params={"model": {"name": name, "css": css}},
            timeout=30,
        )

    def ensure_deck(self, deck_name: str) -> None:
        """Create the named deck in Anki via AnkiConnect.

        Idempotent: if the deck already exists, AnkiConnect returns its
        existing id without error — this method is safe to call unconditionally
        before routing cards to a deck.

        Raises:
            AnkiConnectionError: On connection failure or AnkiConnect error.
        """
        post_action(
            self.config.ankiconnect_url,
            "createDeck",
            params={"deck": deck_name},
            timeout=15,
        )

    def verify_card_target(self) -> None:
        """Validate note type + field mapping, then ensure the deck exists.

        Order is checks-then-side-effects: a failed run creates nothing.

        Raises:
            SetupError: note type missing, or a configured field absent from it.
            AnkiConnectionError: AnkiConnect unreachable or errors.
        """
        models = post_action(self.config.ankiconnect_url, "modelNames", timeout=15) or []
        if self.config.anki_note_type not in models:
            available = ", ".join(models[:5])
            more = "..." if len(models) > 5 else ""
            raise SetupError(
                f"Note type '{self.config.anki_note_type}' not found. "
                f"Available: {available}{more}. "
                f"Check Settings → Anki."
            )

        actual = set(
            post_action(
                self.config.ankiconnect_url,
                "modelFieldNames",
                params={"modelName": self.config.anki_note_type},
                timeout=15,
            )
            or []
        )
        required = {v for v in self.config.anki_fields.values() if v}
        # Validate only the active card-type marker (build_note writes just that
        # one). Inactive markers stay unvalidated so a non-JPMN note type without
        # them still passes pre-flight.
        if self.config.card_type:
            marker = self.config.card_type_marker_fields.get(self.config.card_type, "")
            if marker:
                required.add(marker)
        missing = required - actual
        if missing:
            _sorted_actual = sorted(actual)
            _available = ", ".join(_sorted_actual[:5])
            _more = "..." if len(actual) > 5 else ""
            raise SetupError(
                f"Field(s) {', '.join(sorted(missing))} not found on note type "
                f"'{self.config.anki_note_type}'. "
                f"Available: {_available}{_more}. "
                f"Check Settings → Anki field mapping."
            )

        self.ensure_deck(self.config.anki_deck_name)

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
            safe = deck.replace("\\", "\\\\").replace('"', '\\"').replace("*", "\\*").replace("_", "\\_")
            query += f' -deck:"{safe}"'
        return query

    def get_existing_vocabulary(self) -> set[str]:
        """Get all Japanese vocabulary words already in Anki.

        Queries the collection (minus any ``config.excluded_decks``; see
        :meth:`_build_vocab_query`) and extracts the first field from each note,
        which by Anki convention is always the expression/word being studied.
        Only words containing Japanese characters are included.

        Returns:
            Set of Expression (first-field) values already in the
            collection, dedup-normalized (HTML/media-stripped, NFC) — i.e.
            ``mined_form`` strings, not lemmas. Returns an
            empty set as a graceful-degradation fallback if AnkiConnect
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
            # Find ALL notes in the collection.
            note_ids = _expect_list(
                post_action(
                    self.config.ankiconnect_url,
                    "findNotes",
                    params={"query": self._build_vocab_query()},
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
                self._existing_vocab_cache = set()
                return self._existing_vocab_cache

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

            self._existing_vocab_cache = existing_words
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
            logger.warning("Failed to fetch existing vocabulary (filtering disabled): %s", e)
            return set()

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

    def create_cards_batch(
        self,
        word_data_list: list[CardPayload],
        progress_callback: ProgressCallback | None = None,
    ) -> int:
        """Create multiple Anki cards in batches.

        Args:
            word_data_list: List of CardPayload objects to submit
            progress_callback: Optional callback for progress reporting

        Returns:
            Number of successfully created cards
        """
        if not word_data_list:
            self.last_created_note_ids = []
            self.last_skipped_duplicates = 0
            self.last_updated_notes = 0
            self.last_media_store_failures = 0
            return 0

        self.last_created_note_ids = []
        self.last_skipped_duplicates = 0
        self.last_updated_notes = 0
        self.last_media_store_failures = 0
        skipped_duplicates = 0
        updated_notes = 0
        all_created_ids: list[int] = []

        if progress_callback:
            progress_callback.on_start(len(word_data_list), "Creating Anki cards")

        # First, store all media files and track which succeeded
        stored_files = self._store_media_files_batch(word_data_list)

        # Ship dict-bundled assets referenced by any definition or glossary in
        # the batch via a single batched multi pass. Done up-front so uploads
        # finish before notes reference the filenames; AnkiConnect serializes
        # per-connection, safe.
        self._upload_dict_media_batch(word_data_list)

        # Then create notes in batches. AnkiConnect accepts arbitrary array
        # sizes; 100 cuts round-trips ~2x vs 50 with no observed errors on a
        # representative deck. Larger sizes (200+) show diminishing returns
        # because note construction time inside Anki dominates over HTTP.
        batch_size = 100
        total_created = 0
        # mined_forms of cards actually created (non-null id) this run, for the
        # incremental cache merge in the finally. Only created words are merged —
        # see the rationale there (F10).
        created_forms: list[str] = []
        # Diagnostic counters for the bold path (Issue #20). Surface whether
        # the precomputed bolded strings actually made it to the note body,
        # so users who enable the option but see no bold can tell from the
        # log whether the parse populated the fields.
        bold_used = 0
        bold_fallback = 0

        # Persist progress even if a later batch raises. Earlier batches'
        # cards already exist in Anki; on a mid-run failure we must still
        # record their note IDs (so Undo works) and invalidate the now-stale
        # vocab cache before the error propagates — otherwise those cards are
        # orphaned with no record. The `finally` runs on success AND failure.
        try:
            for i in range(0, len(word_data_list), batch_size):
                batch = word_data_list[i : i + batch_size]

                # Build notes array for this batch (field mapping lives in
                # anki_note_builder).
                notes = []
                for item in batch:
                    built = build_note(item, self.config, stored_files)
                    if built.used_precomputed_bold:
                        bold_used += 1
                    if built.used_bold_fallback:
                        bold_fallback += 1
                    notes.append(built.note)

                # Pre-add duplicate probe (Yomitan partitionAddibleNotes): ask
                # AnkiConnect which of these notes it would reject as duplicates
                # BEFORE submitting, so we skip only real duplicates and submit
                # the rest. `_probe_duplicates` surfaces a genuine (non-duplicate)
                # rejection — bad field mapping, empty first field — as an error
                # rather than silently dropping it; that error propagates to the
                # pipeline boundary (the finally still records earlier batches).
                is_duplicate = self._probe_duplicates(notes)
                submit_notes = [note for note, dup in zip(notes, is_duplicate, strict=True) if not dup]
                submit_payloads = [item for item, dup in zip(batch, is_duplicate, strict=True) if not dup]

                # Duplicate handling (7.4). Default "skip" counts every
                # probe-flagged duplicate as skipped, exactly as before. "update"
                # coalesce-fills each existing note's EMPTY mapped fields (never
                # clobbering user edits) via update_notes_coalesce; only the
                # duplicates it could NOT fill — not locatable, or already fully
                # populated — stay counted as skipped. Updated notes are NOT new
                # notes: their IDs never enter all_created_ids, so the Undo path
                # (delete last_created_note_ids) never deletes a pre-existing user
                # note. Media was uploaded before this loop, so any [sound:]/<img>
                # ref a filled field carries already resolves.
                dup_notes = [note for note, dup in zip(notes, is_duplicate, strict=True) if dup]
                if self.config.duplicate_behavior == "update" and dup_notes:
                    updated = self.update_notes_coalesce(dup_notes)
                    updated_notes += updated
                    skipped_duplicates += len(dup_notes) - updated
                else:
                    skipped_duplicates += len(dup_notes)

                # Submit only the non-duplicates. `post_action` raises
                # `AnkiConnectionError` for connection failures, transport errors,
                # and AnkiConnect-side error payloads. `_expect_list` enforces the
                # addNotes contract: a list of exactly len(submit_notes) slots,
                # each an id (int) or null (None); length alignment is load-bearing
                # for the positional zip below.
                if submit_notes:
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

                if progress_callback:
                    progress_callback.on_progress(
                        min(i + batch_size, len(word_data_list)),
                        f"Cards created: {batch_created}/{len(batch)}",
                    )
        finally:
            # Record whatever batches completed (all of them on success, the
            # earlier ones on a mid-run failure). Runs before the exception
            # re-raises.
            self.last_created_note_ids = all_created_ids
            self.last_skipped_duplicates = skipped_duplicates
            self.last_updated_notes = updated_notes
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

        if progress_callback:
            progress_callback.on_complete()
        if skipped_duplicates > 0:
            logger.info(
                "%d note(s) were not created (likely already in your collection).",
                skipped_duplicates,
            )
        if updated_notes > 0:
            logger.info(
                "%d existing note(s) coalesce-updated (empty mapped fields filled from this run).",
                updated_notes,
            )
        if self.config.bold_target_in_sentence and word_data_list:
            logger.info(
                "bold_target_in_sentence=on: precomputed bold used on %d/%d cards (escape fallback: %d)",
                bold_used,
                len(word_data_list),
                bold_fallback,
            )
        return total_created

    @staticmethod
    def _strip_note_to_first_field(note: dict) -> dict:
        """Return a shallow clone of ``note`` keeping only its first field.

        Ported from Yomitan ``Backend._stripNotesArray``
        (``ext/js/background/backend.js``, upstream e2ed450). Anki dedups on the
        first field only, so shipping the rest — definition/glossary fields can
        carry megabytes of rendered HTML — just to ask "is this a duplicate?"
        wastes bandwidth and AnkiConnect time. Field insertion order is
        preserved by dicts, so the first key is the Expression by construction
        (see ``anki_note_builder.build_note``).
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
        if not notes:
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
                return self._probe_duplicates_fallback(stripped, no_dup)
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
                raise AnkiConnectionError(f"AnkiConnect rejected note {i} (not a duplicate): {error}")
        return is_duplicate

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
        return [w != wo for w, wo in zip(with_dup, without_dup, strict=True)]

    @staticmethod
    def _coalesce_field(existing_value: str, new_value: str) -> str:
        """Merge one field in ``coalesce`` mode: a non-empty existing value wins.

        Ported from Yomitan ``DisplayAnki._getOverwrittenField`` ``'coalesce'``
        case (``ext/js/display/display-anki.js``, upstream e2ed450): the JS
        ``existingValue || newValue`` — an existing user-edited (non-empty) value
        is kept verbatim, and only an empty existing field is filled with the new
        value. Follows JS truthiness exactly, so a whitespace-only existing value
        is treated as non-empty and kept.
        """
        return existing_value or new_value

    @staticmethod
    def _escape_dup_query(text: str) -> str:
        """Strip ``"`` from a findNotes query term (Yomitan ``_escapeQuery``).

        Ported from Yomitan ``AnkiConnect._escapeQuery``
        (``ext/js/comm/anki-connect.js``, upstream e2ed450): the term is wrapped in
        double quotes, so an embedded quote is removed rather than escaped.
        """
        return text.replace('"', "")

    def _find_notes_query_for(self, note: dict) -> str:
        """Build the findNotes query locating ``note``'s existing duplicate.

        Ported from Yomitan ``AnkiConnect._getNoteQuery`` + ``_fieldsToQuery``
        (``ext/js/comm/anki-connect.js``, upstream e2ed450): a first-field-only
        ``"<field>:<value>"`` term (field name lower-cased — Anki field search is
        case-insensitive on the name), optionally prefixed with a ``"deck:..."``
        term for the configured duplicate scope so it searches the same universe
        the pre-add probe used. ``deck-root`` is synthesized client-side via
        :func:`_get_root_deck_name`, exactly as ``build_duplicate_scope_options``
        does for the probe. Returns ``""`` for a fieldless note (never matches;
        the caller then falls back to skip).
        """
        fields = note.get("fields") or {}
        if not fields:
            return ""
        first_key = next(iter(fields))
        field_term = f'"{first_key.lower()}:{self._escape_dup_query(str(fields[first_key]))}"'
        scope = self.config.duplicate_scope
        if scope == "deck":
            deck = self._escape_dup_query(self.config.anki_deck_name)
            return f'"deck:{deck}" {field_term}'
        if scope == "deck-root":
            deck = self._escape_dup_query(_get_root_deck_name(self.config.anki_deck_name))
            return f'"deck:{deck}" {field_term}'
        return field_term

    @staticmethod
    def _unwrap_multi_result(sub: Any) -> Any:
        """Normalize one ``multi`` sub-result to its inner payload.

        AnkiConnect versions differ: a sub-action's slot is either the bare result
        (a list for findNotes, ``None`` for updateNoteFields) or a
        ``{"result": ..., "error": ...}`` wrapper. Mirrors AnkiMediaStore's dual-form
        handling: a truthy ``error`` yields ``None``, otherwise the inner ``result``
        (or the bare value) is returned.
        """
        if isinstance(sub, dict):
            if sub.get("error"):
                return None
            return sub.get("result")
        return sub

    def update_notes_coalesce(self, notes: list[dict]) -> int:
        """Coalesce-fill existing duplicates' empty fields; return the count updated.

        Ported from Yomitan ``DisplayAnki._getOverwrittenNote``
        (``ext/js/display/display-anki.js``) + ``AnkiConnect.findNoteIds``
        (``ext/js/comm/anki-connect.js``), upstream e2ed450. Consumes the built note
        dicts the pre-add probe (7.2) flagged as duplicates and, in ``coalesce``
        mode, fills only the EMPTY mapped fields of the existing note with this
        run's values — a user-edited (non-empty) field is never overwritten.

        Steps (Yomitan ``findNoteIds`` dedup + overwrite merge):
          1. Build one query per note (:meth:`_find_notes_query_for`), deduplicate
             identical queries into a single ``findNotes`` action, and issue them in
             one ``multi`` envelope.
          2. Take each note's first located id as its overwrite target (Yomitan's
             ``noteIds.find(id => id !== INVALID_NOTE_ID)``); a note whose query
             returned nothing falls back to skip (it stays a duplicate). Targets are
             deduped by id so a repeated first field can't double-update one note.
          3. ``notesInfo`` the target ids once, merge field-by-field with
             :meth:`_coalesce_field`, and send ONLY the fields that actually change
             (an empty existing field gaining a non-empty value) via a single
             ``multi`` of ``updateNoteFields`` — a fully-populated existing note is
             left byte-identical and counts as not-updated.

        Updated notes are NOT new notes and are deliberately left out of
        ``last_created_note_ids``; Undo (which deletes those ids) must never delete
        or blank a pre-existing user note that was merely coalesced into.

        Args:
            notes: The built AnkiConnect note dicts flagged as duplicates.

        Returns:
            The number of existing notes actually updated (>= 1 field filled).

        Raises:
            AnkiConnectionError: connection/transport failure or a malformed
                ``notesInfo`` response — consistent with the rest of
                create_cards_batch, whose ``finally`` still records earlier batches.
        """
        if not notes:
            return 0

        # 1. Deduplicate queries (Yomitan findNoteIds actionsTargetsMap): identical
        #    first-field/scope queries share a single findNotes action.
        queries = [self._find_notes_query_for(note) for note in notes]
        unique_queries: list[str] = []
        for q in queries:
            if q and q not in unique_queries:
                unique_queries.append(q)
        if not unique_queries:
            return 0

        find_actions = [{"action": "findNotes", "version": 6, "params": {"query": q}} for q in unique_queries]
        find_results = post_multi(self.config.ankiconnect_url, find_actions, timeout=60)
        query_to_ids: dict[str, list[int]] = {}
        for q, sub in zip(unique_queries, find_results, strict=False):
            payload = self._unwrap_multi_result(sub)
            query_to_ids[q] = [i for i in payload if isinstance(i, int)] if isinstance(payload, list) else []

        # 2. First located id per note is the overwrite target; dedup by id.
        targets: list[tuple[int, dict]] = []  # (note_id, new_fields)
        seen_ids: set[int] = set()
        for note, q in zip(notes, queries, strict=True):
            ids = query_to_ids.get(q, [])
            if not ids:
                continue
            note_id = ids[0]
            if note_id in seen_ids:
                continue
            seen_ids.add(note_id)
            targets.append((note_id, note.get("fields") or {}))
        if not targets:
            return 0

        # 3. Fetch existing fields, merge, update only the fields that change.
        infos = _expect_list(
            post_action(
                self.config.ankiconnect_url,
                "notesInfo",
                params={"notes": [note_id for note_id, _ in targets]},
                timeout=60,
            )
            or [],
            "notesInfo",
            elem_type=dict,
        )
        id_to_fields: dict[int, dict] = {}
        for info in infos:
            note_id = info.get("noteId")
            fields = info.get("fields")
            if isinstance(note_id, int) and isinstance(fields, dict) and fields:
                id_to_fields[note_id] = fields

        update_actions: list[dict] = []
        for note_id, new_fields in targets:
            existing = id_to_fields.get(note_id)
            if not existing:
                # Located by findNotes but gone/deleted by notesInfo time: skip.
                continue
            filled: dict[str, str] = {}
            for field_name, new_value in new_fields.items():
                existing_entry = existing.get(field_name)
                if not isinstance(existing_entry, dict):
                    # Field absent from the existing note (a mapping mismatch): do
                    # not invent it — coalesce only fills fields Anki already has.
                    continue
                existing_value = existing_entry.get("value", "") or ""
                coalesced = self._coalesce_field(existing_value, str(new_value))
                if coalesced != existing_value:
                    filled[field_name] = coalesced
            if filled:
                update_actions.append(
                    {
                        "action": "updateNoteFields",
                        "version": 6,
                        "params": {"note": {"id": note_id, "fields": filled}},
                    }
                )
        if not update_actions:
            return 0

        update_results = post_multi(self.config.ankiconnect_url, update_actions, timeout=60)
        errors = sum(1 for sub in update_results[: len(update_actions)] if isinstance(sub, dict) and sub.get("error"))
        return len(update_actions) - errors

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
        stored = self._media_store.store_batch(word_data_list)
        self.last_media_store_failures = self._media_store.last_store_failures
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
        if not note_ids:
            return 0

        post_action(
            self.config.ankiconnect_url,
            "deleteNotes",
            params={"notes": note_ids},
            timeout=30,
        )
        self.invalidate_existing_vocabulary_cache()
        return len(note_ids)

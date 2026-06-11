"""Service for interacting with Anki via AnkiConnect."""

import base64
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import requests

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError, SetupError
from anki_miner.interfaces import ProgressCallback
from anki_miner.models import CardPayload
from anki_miner.services._ankiconnect import post_action, post_multi
from anki_miner.services.anki_note_builder import (
    OPTIONAL_FIELD_KEYS as _OPTIONAL_FIELD_KEYS,
)
from anki_miner.services.anki_note_builder import (
    REQUIRED_FIELD_KEYS as _REQUIRED_FIELD_KEYS,
)
from anki_miner.services.anki_note_builder import (
    _strip_for_dedup,
    build_note,
)
from anki_miner.services.dictionary.yomitan_renderer import DICT_MEDIA_CLASS

logger = logging.getLogger(__name__)

# Matches any hiragana, katakana, or CJK ideograph (kanji)
_JAPANESE_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3400-\u4DBF]")

# `<img>` tags emitted by yomitan_renderer for dictionary-bundled assets carry
# `class="anki-miner-dict-media"`. Capture the whole tag, then pull `src` out \u2014
# attribute order in the rendered HTML is fixed but a single regex makes the
# scan tolerant of future renderer reshuffles.
_DICT_MEDIA_IMG_RE = re.compile(
    rf'<img\b[^>]*class="[^"]*\b{re.escape(DICT_MEDIA_CLASS)}\b[^"]*"[^>]*>',
    re.IGNORECASE,
)
_IMG_SRC_RE = re.compile(r'src="([^"]+)"', re.IGNORECASE)

# Media uploads are base64-heavy; a smaller chunk than the 100-note addNotes
# batch keeps individual request payloads manageable.
_MEDIA_BATCH_CHUNK = 50
# AnkiConnect resets the connection on very large `multi` request bodies (one
# 50-file chunk of YouTube clips can hit ~7-8 MB of base64), surfacing as a
# requests ConnectionError that reads "Is Anki running?" even though it is.
# Bound each `multi` POST by cumulative base64 size as well as action count so a
# chunk of large files flushes early instead of tripping the reset (Issue: media
# files not stored on big batches).
_MEDIA_BATCH_MAX_BYTES = 4 * 1024 * 1024


def _is_duplicate_error(err: AnkiConnectionError) -> bool:
    """True if an AnkiConnect error payload is (only) about duplicate notes.

    Some AnkiConnect versions surface a duplicate as a top-level ``error`` on
    ``addNotes`` (a string, or a single-element list) instead of a ``null`` slot
    in the result array. We recover from those by retrying per-note; any other
    error (missing deck/model, connection) must keep propagating.
    """
    return "duplicate" in str(err).lower()


def _extract_dict_media_srcs(definition_html: str) -> list[str]:
    """Return every dict-media `src` referenced in a definition HTML blob."""
    if not definition_html:
        return []
    out: list[str] = []
    for tag in _DICT_MEDIA_IMG_RE.findall(definition_html):
        m = _IMG_SRC_RE.search(tag)
        if m:
            out.append(m.group(1))
    return out


def _resolve_dict_media_path(src: str, dicts_root: Path) -> Path | None:
    """Map an Anki-side dict-media filename back to the file on disk.

    The renderer formats src as ``<dict_id>__<flattened-basename>``. dict_id is
    a lowercase-ASCII slug with hyphens (importer guarantees no double-`__`),
    so we split on the first ``__``. The resolved path must stay inside the
    dicts_root tree.
    """
    if "__" not in src:
        return None
    dict_id, _, safe = src.partition("__")
    if not dict_id or not safe or "/" in safe or "\\" in safe or ".." in safe:
        return None
    try:
        root_resolved = dicts_root.resolve()
        candidate = (dicts_root / dict_id / "media" / safe).resolve()
        candidate.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    if not candidate.is_file():
        return None
    return candidate


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
        # Number of notes skipped as duplicates during the last
        # create_cards_batch call (only counts the per-note recovery path; the
        # common case where AnkiConnect returns a null slot is not attributable
        # to a specific cause). Read by the pipeline to report skips.
        self.last_skipped_duplicates: int = 0
        # Number of media files (screenshots/audio) that could not be stored in
        # Anki during the last create_cards_batch call. Read by the pipeline to
        # warn the user when cards land with empty media fields.
        self.last_media_store_failures: int = 0
        # Per-service-lifetime cache of dict-media filenames already shipped to
        # AnkiConnect this run. Avoids re-uploading the same accent SVG once
        # per card across a 5000-word batch.
        self._dict_media_uploaded: set[str] = set()
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
        missing = {v for v in self.config.anki_fields.values() if v} - actual
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
            note_ids = (
                post_action(
                    self.config.ankiconnect_url,
                    "findNotes",
                    params={"query": self._build_vocab_query()},
                    timeout=30,
                )
                or []
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
                notes = (
                    post_action(
                        self.config.ankiconnect_url,
                        "notesInfo",
                        params={"notes": batch},
                        timeout=30,
                    )
                    or []
                )

                for note in notes:
                    fields = note.get("fields", {})
                    if not fields:
                        continue
                    # First field is always the expression/word in Anki
                    # convention. Normalize it the same way Anki dedups (strip
                    # HTML/media, unescape, NFC) so a markup-wrapped Expression
                    # matches the plain `mined_form` the filter compares against
                    # — otherwise the word slips the filter and AnkiConnect
                    # rejects it as a duplicate at addNotes time.
                    first_field = next(iter(fields))
                    word = _strip_for_dedup(fields[first_field].get("value", ""))
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

    def _upload_dict_media_batch(self, word_data_list: list["CardPayload"]) -> None:
        """Batch-upload all dict-media assets referenced across the whole card batch.

        Scans each item's ``definition`` and ``extra_fields["glossary"]`` for
        ``<img class="anki-miner-dict-media" src="…">`` tags, collects the union
        of un-uploaded srcs, resolves each to a file path, and ships them through
        the same pipeline as card screenshots/audio: ``_build_store_media_action``
        → ``_chunk_media_actions`` (count + byte budget) → ``_store_media_chunk``
        (per-file fallback on a failed ``multi`` POST).

        Missing-on-disk srcs are logged as warnings and added to
        ``_dict_media_uploaded`` so they are not retried on every card (identical
        to the old per-card behavior). Otherwise a src is cached only after a
        confirmed successful store — a failed upload stays uncached so the next
        batch retries it.
        """
        # Collect un-uploaded srcs across the whole batch (ordered, deduped).
        seen: set[str] = set()
        all_srcs: list[str] = []
        for item in word_data_list:
            for html_field in (
                item.definition,
                item.extra_fields.get("glossary") if item.extra_fields else None,
            ):
                if not isinstance(html_field, str):
                    continue
                for src in _extract_dict_media_srcs(html_field):
                    if src not in self._dict_media_uploaded and src not in seen:
                        seen.add(src)
                        all_srcs.append(src)

        if not all_srcs:
            return

        # Resolve each src; cache missing ones now so we don't retry.
        items: list[tuple[str, dict]] = []
        for src in all_srcs:
            file_path = _resolve_dict_media_path(src, self.config.dicts_root)
            if file_path is None:
                logger.warning("Dict media file missing on disk: %s", src)
                # Cache anyway so we don't retry every card.
                self._dict_media_uploaded.add(src)
                continue
            action = self._build_store_media_action(src, file_path)
            if action is not None:
                items.append((src, action))

        # Shared with the screenshot/audio path: chunks bounded by action count
        # AND base64 byte budget, per-file fallback when a multi POST trips the
        # oversized-body connection reset. _store_media_chunk returns only the
        # srcs confirmed stored, so failures stay uncached and retry next batch.
        for chunk in self._chunk_media_actions(items):
            self._dict_media_uploaded |= self._store_media_chunk(chunk)

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
            self.last_media_store_failures = 0
            return 0

        self.last_created_note_ids = []
        self.last_skipped_duplicates = 0
        self.last_media_store_failures = 0
        skipped_duplicates = 0
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

                # Send batch request. `post_action` raises `AnkiConnectionError`
                # for connection failures, transport errors, and AnkiConnect-side
                # error payloads. Duplicates normally come back as a `null` slot in
                # the result array (batch survives), but some AnkiConnect versions
                # raise a top-level duplicate error for the whole batch — which used
                # to abort the entire run with zero cards. Recover from that case by
                # retrying per-note and skipping only the duplicates; any other
                # error still propagates to the pipeline boundary.
                try:
                    note_ids = (
                        post_action(
                            self.config.ankiconnect_url,
                            "addNotes",
                            params={"notes": notes},
                            timeout=60,
                        )
                        or []
                    )
                except AnkiConnectionError as e:
                    if not _is_duplicate_error(e):
                        raise
                    logger.warning(
                        "addNotes reported a duplicate for the batch; retrying per-note "
                        "and skipping duplicates already in your collection.",
                    )
                    note_ids, batch_skipped = self._add_notes_individually(notes)
                    skipped_duplicates += batch_skipped

                # Count successful creations (non-null IDs)
                batch_created = sum(1 for nid in note_ids if nid is not None)
                total_created += batch_created
                all_created_ids.extend(nid for nid in note_ids if nid is not None)

                if progress_callback:
                    progress_callback.on_progress(
                        min(i + batch_size, len(word_data_list)),
                        f"Cards created: {batch_created}/{len(batch)}",
                    )
        finally:
            # Record whatever batches completed (all of them on success, the
            # earlier ones on a mid-run failure) and invalidate the vocab
            # cache if any card was created so the filter reflects the new
            # collection. Runs before the exception re-raises.
            self.last_created_note_ids = all_created_ids
            self.last_skipped_duplicates = skipped_duplicates
            if total_created > 0:
                self.invalidate_existing_vocabulary_cache()

        if progress_callback:
            progress_callback.on_complete()
        if skipped_duplicates > 0:
            logger.info(
                "Skipped %d note(s) Anki flagged as duplicates (existing card or same-batch).", skipped_duplicates
            )
        if self.config.bold_target_in_sentence and word_data_list:
            logger.info(
                "bold_target_in_sentence=on: precomputed bold used on %d/%d cards (escape fallback: %d)",
                bold_used,
                len(word_data_list),
                bold_fallback,
            )
        return total_created

    def _add_notes_individually(self, notes: list[dict]) -> tuple[list[int | None], int]:
        """Add notes one at a time, skipping any AnkiConnect rejects as duplicates.

        Fallback for the case where ``addNotes`` raises a top-level duplicate
        error for the whole batch instead of returning a ``null`` slot per
        duplicate. Returns the per-note id list (``None`` for a skipped
        duplicate) and the count of duplicates skipped. A non-duplicate error on
        any note propagates so genuine failures (missing deck/model, connection
        loss) are not silently swallowed.
        """
        results: list[int | None] = []
        skipped = 0
        for note in notes:
            try:
                note_id = post_action(
                    self.config.ankiconnect_url,
                    "addNote",
                    params={"note": note},
                    timeout=60,
                )
                results.append(note_id)
            except AnkiConnectionError as e:
                if not _is_duplicate_error(e):
                    raise
                results.append(None)
                skipped += 1
        return results, skipped

    def _store_media_files_batch(
        self,
        word_data_list: list[CardPayload],
    ) -> set[str]:
        """Store all media files in Anki collection via batched ``multi`` POSTs.

        Collects all readable (filename, base64-data) pairs, deduplicates by
        filename, then sends them in chunks bounded by both ``_MEDIA_BATCH_CHUNK``
        actions and ``_MEDIA_BATCH_MAX_BYTES`` of cumulative base64 payload per
        ``multi`` call.  Files that cannot be read (OSError) are logged and
        skipped at build time.  If a chunk's ``multi`` POST fails with a transport
        error (AnkiConnect resets the connection on oversized bodies), the chunk
        is retried one file at a time via single ``storeMediaFile`` POSTs.
        Per-sub-action AnkiConnect errors (sub-result with an ``"error"`` key)
        exclude that filename from the returned set.

        Sets ``self.last_media_store_failures`` to the count of files that could
        not be stored so callers can surface it to the user instead of silently
        creating cards with empty media fields.

        Args:
            word_data_list: List of CardPayload objects whose media should be uploaded

        Returns:
            Set of filenames that were successfully stored
        """
        # Build (filename → action) mapping, deduped by filename (last writer
        # wins, matching the old set-based dedup semantics).
        actions_by_filename: dict[str, dict] = {}
        for item in word_data_list:
            media = item.media
            for filename, src_path in [
                (media.screenshot_filename, media.screenshot_path),
                (media.audio_filename, media.audio_path),
            ]:
                if not filename or not src_path or not src_path.exists():
                    continue
                action = self._build_store_media_action(filename, src_path)
                if action is not None:
                    actions_by_filename[filename] = action

        if not actions_by_filename:
            self.last_media_store_failures = 0
            return set()

        stored: set[str] = set()
        for chunk in self._chunk_media_actions(list(actions_by_filename.items())):
            stored |= self._store_media_chunk(chunk)

        self.last_media_store_failures = len(actions_by_filename) - len(stored)
        return stored

    def _chunk_media_actions(self, items: list[tuple[str, dict]]) -> Iterator[list[tuple[str, dict]]]:
        """Yield (filename, action) sublists bounded by count and base64 byte budget.

        Flushes the current chunk before adding an action that would push it past
        ``_MEDIA_BATCH_CHUNK`` actions or ``_MEDIA_BATCH_MAX_BYTES`` of base64
        data. A single action larger than the byte budget still ships alone.
        """
        chunk: list[tuple[str, dict]] = []
        chunk_bytes = 0
        for filename, action in items:
            action_bytes = len(action["params"].get("data", ""))
            if chunk and (len(chunk) >= _MEDIA_BATCH_CHUNK or chunk_bytes + action_bytes > _MEDIA_BATCH_MAX_BYTES):
                yield chunk
                chunk = []
                chunk_bytes = 0
            chunk.append((filename, action))
            chunk_bytes += action_bytes
        if chunk:
            yield chunk

    def _store_media_chunk(self, chunk: list[tuple[str, dict]]) -> set[str]:
        """Store one chunk via ``multi``; fall back to per-file POSTs on transport failure."""
        filenames = [f for f, _ in chunk]
        actions = [a for _, a in chunk]
        try:
            sub_results = post_multi(self.config.ankiconnect_url, actions, timeout=30)
        except AnkiConnectionError as e:
            cause = e.__cause__
            logger.warning(
                "Media batch multi POST failed (%s: %s); retrying %d file(s) individually",
                type(cause).__name__ if cause is not None else type(e).__name__,
                e,
                len(actions),
            )
            return self._store_media_files_individually(chunk)

        if len(sub_results) != len(actions):
            logger.warning(
                "post_multi returned %d results for %d actions; some files may be silently skipped",
                len(sub_results),
                len(actions),
            )
        stored: set[str] = set()
        for filename, sub_result in zip(filenames, sub_results, strict=False):
            if not (isinstance(sub_result, dict) and sub_result.get("error")):
                stored.add(filename)
        return stored

    def _store_media_files_individually(self, chunk: list[tuple[str, dict]]) -> set[str]:
        """Per-file ``storeMediaFile`` fallback (tiny bodies) for a failed-multi chunk.

        This is the pre-batching upload path: each file goes in its own small POST,
        which avoids the oversized-body connection reset that breaks the ``multi``
        envelope. Files AnkiConnect still rejects are logged and excluded.
        """
        stored: set[str] = set()
        for filename, action in chunk:
            try:
                post_action(
                    self.config.ankiconnect_url,
                    "storeMediaFile",
                    params=action["params"],
                    timeout=30,
                )
                stored.add(filename)
            except AnkiConnectionError as e:
                logger.warning("Failed to store media file %s individually: %s", filename, e)
        return stored

    def _build_store_media_action(self, filename: str, src_path: Path) -> dict | None:
        """Build a ``storeMediaFile`` action dict for use in a ``multi`` envelope.

        Returns ``None`` and logs a warning if the file cannot be read.
        """
        try:
            with open(src_path, "rb") as f:
                data_base64 = base64.b64encode(f.read()).decode("utf-8")
        except OSError as e:
            logger.warning(f"Failed to read media file {filename}: {e}")
            return None
        return {
            "action": "storeMediaFile",
            "version": 6,
            "params": {"filename": filename, "data": data_base64},
        }

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

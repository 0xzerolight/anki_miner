"""Service for interacting with Anki via AnkiConnect."""

import base64
import html
import logging
import re
import unicodedata
from pathlib import Path

import requests

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.interfaces import ProgressCallback
from anki_miner.models import CardPayload
from anki_miner.services._ankiconnect import post_action
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
    """
    text = _SOUND_REF_RE.sub("", value)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFC", text)
    return " ".join(text.split())


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
        "frequency",
    }

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
            Set of words (lemmas) already in the collection. Returns an
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

    def store_media_file(self, filename: str, filepath: Path) -> bool:
        """Store a media file in Anki's collection.

        Args:
            filename: Filename to use in Anki
            filepath: Path to the file to store

        Returns:
            True if successful, False otherwise
        """
        try:
            with open(filepath, "rb") as f:
                data_b64 = base64.b64encode(f.read()).decode("utf-8")
        except OSError:
            return False
        try:
            post_action(
                self.config.ankiconnect_url,
                "storeMediaFile",
                params={"filename": filename, "data": data_b64},
                timeout=30,
            )
        except AnkiConnectionError:
            return False
        return True

    def _upload_dict_media(self, definition_html: str | None) -> None:
        """Ship any dict-media files referenced in `definition_html` to Anki.

        Walks `<img class="anki-miner-dict-media" src="X">` tags, resolves each
        src back to a file under ``config.dicts_root/<dict_id>/media/``, and
        uploads via storeMediaFile. Results are cached so the same SVG is sent
        at most once per AnkiService lifetime.
        """
        if not definition_html:
            return
        srcs = _extract_dict_media_srcs(definition_html)
        for src in srcs:
            if src in self._dict_media_uploaded:
                continue
            file_path = _resolve_dict_media_path(src, self.config.dicts_root)
            if file_path is None:
                logger.warning("Dict media file missing on disk: %s", src)
                # Cache anyway so we don't retry every card.
                self._dict_media_uploaded.add(src)
                continue
            if self.store_media_file(src, file_path):
                self._dict_media_uploaded.add(src)
            else:
                logger.warning("Failed to store dict media file: %s", src)

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
            return 0

        self.last_created_note_ids = []
        self.last_skipped_duplicates = 0
        skipped_duplicates = 0
        all_created_ids: list[int] = []

        if progress_callback:
            progress_callback.on_start(len(word_data_list), "Creating Anki cards")

        # First, store all media files and track which succeeded
        stored_files = self._store_media_files_batch(word_data_list)

        # Ship dict-bundled assets referenced by any definition or glossary in
        # the batch. Done up-front so storeMediaFile races finish before notes
        # reference the filenames; AnkiConnect serializes per-connection, safe.
        for item in word_data_list:
            self._upload_dict_media(item.definition)
            if item.extra_fields and isinstance(item.extra_fields.get("glossary"), str):
                self._upload_dict_media(item.extra_fields["glossary"])

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

        for i in range(0, len(word_data_list), batch_size):
            batch = word_data_list[i : i + batch_size]

            # Build notes array for this batch
            notes = []
            for item in batch:
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

                # Build field values (only reference successfully stored media)
                picture_html = ""
                if media.screenshot_filename and media.screenshot_filename in stored_files:
                    picture_html = f'<img src="{html.escape(media.screenshot_filename)}">'

                audio_ref = ""
                if media.audio_filename and media.audio_filename in stored_files:
                    audio_ref = f"[sound:{media.audio_filename}]"

                # Sentence + SentenceFurigana use the bolded forms when the
                # config flag is on AND the parse pre-computed them. The
                # precomputed forms are already HTML-safe (per-token escape
                # in wrap_target_*); the <b> tags must not be double-escaped.
                # Empty precomputed string means "fall back to escape" — this
                # is the path for entries that came from a code path that
                # did not honor the bold flag (defensive).
                if self.config.bold_target_in_sentence and word.sentence_bolded:
                    sentence_field = word.sentence_bolded
                    bold_used += 1
                else:
                    sentence_field = html.escape(word.sentence)
                    if self.config.bold_target_in_sentence:
                        bold_fallback += 1
                if self.config.bold_target_in_sentence and word.sentence_furigana_bolded:
                    sentence_furigana_field = word.sentence_furigana_bolded
                else:
                    sentence_furigana_field = html.escape(word.sentence_furigana)

                # Build fields, skipping any with empty config mapping
                field_data = {
                    "word": html.escape(word.mined_form),
                    "sentence": sentence_field,
                    "definition": definition or "",
                    "glossary": glossary_html,
                    "picture": picture_html,
                    "audio": audio_ref,
                    "expression_furigana": html.escape(word.expression_furigana),
                    "expression_reading": html.escape(word.expression_reading),
                    "sentence_furigana": sentence_furigana_field,
                    "sentence_reading": html.escape(word.sentence_reading),
                }
                fields = {}
                for key, value in field_data.items():
                    anki_field_name = self.config.anki_fields.get(key, "")
                    if anki_field_name:
                        fields[anki_field_name] = value

                # Add optional fields if configured and data available
                if extra_fields:
                    for key, value in extra_fields.items():
                        anki_field_name = self.config.anki_fields.get(key, "")
                        if key in self.OPTIONAL_FIELD_KEYS and anki_field_name and value:
                            fields[anki_field_name] = html.escape(str(value))

                notes.append(
                    {
                        "deckName": self.config.anki_deck_name,
                        "modelName": self.config.anki_note_type,
                        "fields": fields,
                        "tags": self.config.anki_tags.split(),
                    }
                )

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

        if progress_callback:
            progress_callback.on_complete()

        self.last_created_note_ids = all_created_ids
        self.last_skipped_duplicates = skipped_duplicates
        if total_created > 0:
            self.invalidate_existing_vocabulary_cache()
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
        """Store all media files in Anki collection.

        Args:
            word_data_list: List of CardPayload objects whose media should be uploaded

        Returns:
            Set of filenames that were successfully stored
        """
        stored: set[str] = set()
        batch_size = 50

        for i in range(0, len(word_data_list), batch_size):
            batch = word_data_list[i : i + batch_size]

            for item in batch:
                media = item.media
                for filename, src_path in [
                    (media.screenshot_filename, media.screenshot_path),
                    (media.audio_filename, media.audio_path),
                ]:
                    if filename and src_path and src_path.exists() and self._store_one_media(filename, src_path):
                        stored.add(filename)

        return stored

    def _store_one_media(self, filename: str, src_path: Path) -> bool:
        """Upload one media file via AnkiConnect. Returns True on success.

        On failure (file read error, AnkiConnect error), logs and returns False.
        """
        try:
            with open(src_path, "rb") as f:
                data_base64 = base64.b64encode(f.read()).decode("utf-8")
            post_action(
                self.config.ankiconnect_url,
                "storeMediaFile",
                params={
                    "filename": filename,
                    "data": data_base64,
                },
                timeout=30,
            )
            return True
        except (AnkiConnectionError, OSError) as e:
            logger.warning(f"Failed to store media file {filename}: {e}")
            return False

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

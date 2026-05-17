"""Service for interacting with Anki via AnkiConnect."""

import base64
import html
import logging
import re
from pathlib import Path

import requests

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.interfaces import ProgressCallback
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
        # Per-service-lifetime cache of dict-media filenames already shipped to
        # AnkiConnect this run. Avoids re-uploading the same accent SVG once
        # per card across a 5000-word batch.
        self._dict_media_uploaded: set[str] = set()

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

    def get_existing_vocabulary(self) -> set[str]:
        """Get all Japanese vocabulary words already in Anki across ALL decks.

        Queries the entire collection and extracts the first field from each note,
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
        try:
            # Find ALL notes in the collection.
            note_ids = (
                post_action(
                    self.config.ankiconnect_url,
                    "findNotes",
                    params={"query": "deck:*"},
                    timeout=30,
                )
                or []
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
                    # First field is always the expression/word in Anki convention
                    first_field = next(iter(fields))
                    word = fields[first_field].get("value", "").strip()
                    if word and _JAPANESE_RE.search(word):
                        existing_words.add(word)

            return existing_words

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
        word_data_list: list[tuple],
        progress_callback: ProgressCallback | None = None,
    ) -> int:
        """Create multiple Anki cards in batches.

        Args:
            word_data_list: List of (word, media, definition) or
                            (word, media, definition, extra_fields) tuples
            progress_callback: Optional callback for progress reporting

        Returns:
            Number of successfully created cards
        """
        if not word_data_list:
            self.last_created_note_ids = []
            return 0

        self.last_created_note_ids = []
        all_created_ids: list[int] = []

        if progress_callback:
            progress_callback.on_start(len(word_data_list), "Creating Anki cards")

        # First, store all media files and track which succeeded
        stored_files = self._store_media_files_batch(word_data_list)

        # Ship dict-bundled assets referenced by any definition or glossary in
        # the batch. Done up-front so storeMediaFile races finish before notes
        # reference the filenames; AnkiConnect serializes per-connection, safe.
        for item in word_data_list:
            definition = item[2] if len(item) > 2 else None
            extra_fields = item[3] if len(item) > 3 else None
            self._upload_dict_media(definition if isinstance(definition, str) else None)
            if extra_fields and isinstance(extra_fields.get("glossary"), str):
                self._upload_dict_media(extra_fields["glossary"])

        # Then create notes in batches
        batch_size = 50
        total_created = 0

        for i in range(0, len(word_data_list), batch_size):
            batch = word_data_list[i : i + batch_size]

            # Build notes array for this batch
            notes = []
            for item in batch:
                # Support both 3-tuples and 4-tuples for backwards compatibility
                if len(item) == 4:
                    word, media, definition, extra_fields = item
                else:
                    word, media, definition = item
                    extra_fields = None

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

                # Build fields, skipping any with empty config mapping
                field_data = {
                    "word": html.escape(word.surface),
                    "sentence": html.escape(word.sentence),
                    "definition": definition or "",
                    "glossary": glossary_html,
                    "picture": picture_html,
                    "audio": audio_ref,
                    "expression_furigana": html.escape(word.expression_furigana),
                    "expression_reading": html.escape(word.expression_reading),
                    "sentence_furigana": html.escape(word.sentence_furigana),
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
            # error payloads — propagate them; callers catch at the
            # pipeline boundary.
            note_ids = (
                post_action(
                    self.config.ankiconnect_url,
                    "addNotes",
                    params={"notes": notes},
                    timeout=60,
                )
                or []
            )

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
        return total_created

    def _store_media_files_batch(
        self,
        word_data_list: list[tuple],
    ) -> set[str]:
        """Store all media files in Anki collection.

        Args:
            word_data_list: List of (word, media, definition[, extra_fields]) tuples

        Returns:
            Set of filenames that were successfully stored
        """
        stored: set[str] = set()
        batch_size = 50

        for i in range(0, len(word_data_list), batch_size):
            batch = word_data_list[i : i + batch_size]

            for item in batch:
                media = item[1]  # media is always the second element
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
        return len(note_ids)

"""Service for interacting with Anki via AnkiConnect."""

import base64
import html
import logging
from pathlib import Path

import requests

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.interfaces import ProgressCallback
from anki_miner.models import MediaData, TokenizedWord

logger = logging.getLogger(__name__)


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
        "pitch_accent",
        "frequency_rank",
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
            response = requests.post(
                self.config.ankiconnect_url,
                json={
                    "action": "modelFieldNames",
                    "version": 6,
                    "params": {"modelName": name},
                },
                timeout=15,
            )
            result = response.json()
            if result.get("error"):
                return []
            return result.get("result", [])
        except (requests.RequestException, ValueError):
            return []

    def get_existing_vocabulary(self) -> set[str]:
        """Get all vocabulary words already in Anki across ALL decks.

        Returns:
            Set of words (lemmas) already in the collection

        Raises:
            AnkiConnectionError: If cannot connect to AnkiConnect
        """
        try:
            # Find all notes with the word field
            response = requests.post(
                self.config.ankiconnect_url,
                json={
                    "action": "findNotes",
                    "version": 6,
                    "params": {"query": f"{self.config.anki_word_field}:*"},
                },
                timeout=30,
            )

            result = response.json()
            if result.get("error"):
                raise AnkiConnectionError(
                    f"AnkiConnect error while finding notes: {result['error']}"
                )

            note_ids = result.get("result", [])

            if not note_ids:
                return set()

            # Get note info for all notes
            response = requests.post(
                self.config.ankiconnect_url,
                json={
                    "action": "notesInfo",
                    "version": 6,
                    "params": {"notes": note_ids},
                },
                timeout=60,
            )

            result = response.json()
            if result.get("error"):
                raise AnkiConnectionError(
                    f"AnkiConnect error while getting notes info: {result['error']}"
                )

            notes = result.get("result", [])

            # Extract words from the word field
            existing_words = set()
            word_field = self.config.anki_word_field

            for note in notes:
                fields = note.get("fields", {})
                if word_field in fields:
                    word = fields[word_field].get("value", "").strip()
                    if word:
                        existing_words.add(word)

            return existing_words

        except requests.exceptions.ConnectionError as e:
            raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?") from e
        except (requests.RequestException, ValueError):
            # Return empty set on error (non-fatal)
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

            response = requests.post(
                self.config.ankiconnect_url,
                json={
                    "action": "storeMediaFile",
                    "version": 6,
                    "params": {"filename": filename, "data": data_b64},
                },
                timeout=30,
            )

            result = response.json()
            return not result.get("error")

        except (requests.RequestException, OSError, ValueError):
            return False

    def create_card(
        self,
        word: TokenizedWord,
        media: MediaData,
        definition: str | None,
        extra_fields: dict[str, str] | None = None,
    ) -> bool:
        """Create a single Anki card.

        Args:
            word: TokenizedWord with word information
            media: MediaData with media file paths
            definition: HTML-formatted definition (optional)
            extra_fields: Optional dict of extra field data (e.g. pitch_accent, frequency_rank)

        Returns:
            True if card created successfully, False otherwise
        """
        # Store media files in Anki
        screenshot_stored = False
        audio_stored = False
        if media.screenshot_path and media.screenshot_filename:
            screenshot_stored = self.store_media_file(
                media.screenshot_filename, media.screenshot_path
            )

        if media.audio_path and media.audio_filename:
            audio_stored = self.store_media_file(media.audio_filename, media.audio_path)

        # Build field values (only reference successfully stored media)
        picture_html = ""
        if media.screenshot_filename and screenshot_stored:
            picture_html = f'<img src="{html.escape(media.screenshot_filename)}">'

        audio_ref = ""
        if media.audio_filename and audio_stored:
            audio_ref = f"[sound:{media.audio_filename}]"

        # Build fields, skipping any with empty config mapping
        field_data = {
            "word": html.escape(word.lemma),
            "sentence": html.escape(word.sentence),
            "definition": definition or "",
            "picture": picture_html,
            "audio": audio_ref,
            "expression_furigana": html.escape(word.expression_furigana),
            "sentence_furigana": html.escape(word.sentence_furigana),
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

        # Create note
        try:
            response = requests.post(
                self.config.ankiconnect_url,
                json={
                    "action": "addNote",
                    "version": 6,
                    "params": {
                        "note": {
                            "deckName": self.config.anki_deck_name,
                            "modelName": self.config.anki_note_type,
                            "fields": fields,
                            "tags": ["auto-mined"],
                        }
                    },
                },
                timeout=30,
            )

            result = response.json()
            return not result.get("error")

        except (requests.RequestException, OSError, ValueError):
            return False

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

                # Build field values (only reference successfully stored media)
                picture_html = ""
                if media.screenshot_filename and media.screenshot_filename in stored_files:
                    picture_html = f'<img src="{html.escape(media.screenshot_filename)}">'

                audio_ref = ""
                if media.audio_filename and media.audio_filename in stored_files:
                    audio_ref = f"[sound:{media.audio_filename}]"

                # Build fields, skipping any with empty config mapping
                field_data = {
                    "word": html.escape(word.lemma),
                    "sentence": html.escape(word.sentence),
                    "definition": definition or "",
                    "picture": picture_html,
                    "audio": audio_ref,
                    "expression_furigana": html.escape(word.expression_furigana),
                    "sentence_furigana": html.escape(word.sentence_furigana),
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
                        "tags": ["auto-mined"],
                    }
                )

            # Send batch request
            try:
                response = requests.post(
                    self.config.ankiconnect_url,
                    json={
                        "action": "addNotes",
                        "version": 6,
                        "params": {"notes": notes},
                    },
                    timeout=60,
                )

                result = response.json()
                if not result.get("error"):
                    # Count successful creations (non-null IDs)
                    note_ids = result.get("result", [])
                    batch_created = sum(1 for nid in note_ids if nid is not None)
                    total_created += batch_created
                    all_created_ids.extend(nid for nid in note_ids if nid is not None)

                    if progress_callback:
                        progress_callback.on_progress(
                            min(i + batch_size, len(word_data_list)),
                            f"Cards created: {batch_created}/{len(batch)}",
                        )
                else:
                    if progress_callback:
                        progress_callback.on_error(
                            f"Batch {i // batch_size + 1}", result.get("error")
                        )

            except Exception as e:
                if progress_callback:
                    progress_callback.on_error(f"Batch {i // batch_size + 1}", str(e))

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
                # Store screenshot
                if (
                    media.screenshot_path
                    and media.screenshot_filename
                    and media.screenshot_path.exists()
                ):
                    try:
                        with open(media.screenshot_path, "rb") as f:
                            screenshot_base64 = base64.b64encode(f.read()).decode("utf-8")

                        response = requests.post(
                            self.config.ankiconnect_url,
                            json={
                                "action": "storeMediaFile",
                                "version": 6,
                                "params": {
                                    "filename": media.screenshot_filename,
                                    "data": screenshot_base64,
                                },
                            },
                            timeout=30,
                        )
                        if not response.json().get("error"):
                            stored.add(media.screenshot_filename)
                    except (requests.RequestException, OSError, ValueError) as e:
                        logger.warning(
                            f"Failed to store screenshot {media.screenshot_filename}: {e}"
                        )

                # Store audio
                if media.audio_path and media.audio_filename and media.audio_path.exists():
                    try:
                        with open(media.audio_path, "rb") as f:
                            audio_base64 = base64.b64encode(f.read()).decode("utf-8")

                        response = requests.post(
                            self.config.ankiconnect_url,
                            json={
                                "action": "storeMediaFile",
                                "version": 6,
                                "params": {
                                    "filename": media.audio_filename,
                                    "data": audio_base64,
                                },
                            },
                            timeout=30,
                        )
                        if not response.json().get("error"):
                            stored.add(media.audio_filename)
                    except (requests.RequestException, OSError, ValueError) as e:
                        logger.warning(f"Failed to store audio {media.audio_filename}: {e}")

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
            AnkiConnectionError: If cannot connect to AnkiConnect or deletion fails
        """
        if not note_ids:
            return 0

        try:
            response = requests.post(
                self.config.ankiconnect_url,
                json={
                    "action": "deleteNotes",
                    "version": 6,
                    "params": {"notes": note_ids},
                },
                timeout=30,
            )

            result = response.json()
            if result.get("error"):
                raise AnkiConnectionError(f"Failed to delete notes: {result['error']}")

            return len(note_ids)

        except requests.exceptions.ConnectionError as e:
            raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?") from e

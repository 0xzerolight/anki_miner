"""Tests for anki_service module."""

import base64
from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.models import MediaData
from anki_miner.services.anki_service import AnkiService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(result=None, error=None):
    """Create a mock requests.Response with the given AnkiConnect JSON body."""
    resp = MagicMock()
    resp.json.return_value = {"result": result, "error": error}
    return resp


# ---------------------------------------------------------------------------
# TestGetExistingVocabulary
# ---------------------------------------------------------------------------


class TestGetExistingVocabulary:
    """Tests for AnkiService.get_existing_vocabulary."""

    def test_success_with_multiple_notes(self, test_config):
        """Should return a set of words from multiple notes."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1, 2, 3])
        notes_resp = _mock_response(
            result=[
                {"fields": {"word": {"value": "食べる"}}},
                {"fields": {"word": {"value": "飲む"}}},
                {"fields": {"word": {"value": "走る"}}},
            ]
        )

        with patch("requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"食べる", "飲む", "走る"}

    def test_empty_collection(self, test_config):
        """Should return empty set when no note IDs are returned."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[])

        with patch("requests.post", return_value=find_resp):
            result = service.get_existing_vocabulary()

        assert result == set()

    def test_find_notes_error_response(self, test_config):
        """Should raise AnkiConnectionError when findNotes returns an error."""
        service = AnkiService(test_config)

        find_resp = _mock_response(error="Invalid query")

        with (
            patch("requests.post", return_value=find_resp),
            pytest.raises(AnkiConnectionError),
        ):
            service.get_existing_vocabulary()

    def test_notes_info_error_response(self, test_config):
        """Should raise AnkiConnectionError when notesInfo returns an error."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1, 2])
        notes_resp = _mock_response(error="Something went wrong")

        with (
            patch("requests.post", side_effect=[find_resp, notes_resp]),
            pytest.raises(AnkiConnectionError),
        ):
            service.get_existing_vocabulary()

    def test_connection_error_raises_anki_connection_error(self, test_config):
        """Should raise AnkiConnectionError on ConnectionError."""
        service = AnkiService(test_config)

        with (
            patch("requests.post", side_effect=requests.exceptions.ConnectionError()),
            pytest.raises(AnkiConnectionError, match="Cannot connect"),
        ):
            service.get_existing_vocabulary()

    def test_request_exception_returns_empty_set(self, test_config):
        """Should return empty set on generic RequestException."""
        service = AnkiService(test_config)

        with patch("requests.post", side_effect=requests.exceptions.Timeout()):
            result = service.get_existing_vocabulary()

        assert result == set()

    def test_value_error_returns_empty_set(self, test_config):
        """Should return empty set on ValueError (e.g., bad JSON)."""
        service = AnkiService(test_config)

        bad_resp = MagicMock()
        bad_resp.json.side_effect = ValueError("No JSON")

        with patch("requests.post", return_value=bad_resp):
            result = service.get_existing_vocabulary()

        assert result == set()

    def test_skips_empty_field_values(self, test_config):
        """Should skip notes where the word field value is empty."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1, 2, 3])
        notes_resp = _mock_response(
            result=[
                {"fields": {"word": {"value": "食べる"}}},
                {"fields": {"word": {"value": ""}}},
                {"fields": {"word": {"value": "走る"}}},
            ]
        )

        with patch("requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"食べる", "走る"}

    def test_skips_whitespace_field_values(self, test_config):
        """Should skip notes where the word field value is only whitespace."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1, 2])
        notes_resp = _mock_response(
            result=[
                {"fields": {"word": {"value": "   "}}},
                {"fields": {"word": {"value": "飲む"}}},
            ]
        )

        with patch("requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"飲む"}

    def test_uses_configured_word_field_name(self, temp_dir):
        """Should query and extract using the configured word field name."""
        from anki_miner.config import AnkiMinerConfig

        config = AnkiMinerConfig(
            anki_word_field="Expression",
            anki_fields={
                "word": "Expression",
                "sentence": "Sentence",
                "definition": "Definition",
                "picture": "Picture",
                "audio": "Audio",
                "expression_furigana": "ExpressionFurigana",
                "sentence_furigana": "SentenceFurigana",
            },
            media_temp_folder=temp_dir / "temp",
            jmdict_path=temp_dir / "dict",
        )
        service = AnkiService(config)

        find_resp = _mock_response(result=[1])
        notes_resp = _mock_response(
            result=[
                {"fields": {"Expression": {"value": "見る"}}},
            ]
        )

        with patch("requests.post", side_effect=[find_resp, notes_resp]) as mock_post:
            result = service.get_existing_vocabulary()

        # Verify the findNotes query used the configured field name
        find_call_payload = mock_post.call_args_list[0][1]["json"]
        assert find_call_payload["params"]["query"] == "Expression:*"

        assert result == {"見る"}


# ---------------------------------------------------------------------------
# TestStoreMediaFile
# ---------------------------------------------------------------------------


class TestStoreMediaFile:
    """Tests for AnkiService.store_media_file."""

    def test_success_verifies_base64(self, test_config, tmp_path):
        """Should read the file, base64-encode it, and return True on success."""
        service = AnkiService(test_config)
        filepath = tmp_path / "test.jpg"
        file_content = b"\xff\xd8fake-jpeg-data"
        filepath.write_bytes(file_content)

        resp = _mock_response(result="test.jpg")

        with patch("requests.post", return_value=resp) as mock_post:
            result = service.store_media_file("test.jpg", filepath)

        assert result is True

        payload = mock_post.call_args[1]["json"]
        assert payload["action"] == "storeMediaFile"
        assert payload["version"] == 6
        assert payload["params"]["filename"] == "test.jpg"
        expected_b64 = base64.b64encode(file_content).decode("utf-8")
        assert payload["params"]["data"] == expected_b64

    def test_anki_error_response_returns_false(self, test_config, tmp_path):
        """Should return False when AnkiConnect reports an error."""
        service = AnkiService(test_config)
        filepath = tmp_path / "test.jpg"
        filepath.write_bytes(b"data")

        resp = _mock_response(error="Permission denied")

        with patch("requests.post", return_value=resp):
            result = service.store_media_file("test.jpg", filepath)

        assert result is False

    def test_request_exception_returns_false(self, test_config, tmp_path):
        """Should return False on RequestException."""
        service = AnkiService(test_config)
        filepath = tmp_path / "test.jpg"
        filepath.write_bytes(b"data")

        with patch("requests.post", side_effect=requests.exceptions.ConnectionError()):
            result = service.store_media_file("test.jpg", filepath)

        assert result is False

    def test_file_not_found_returns_false(self, test_config, tmp_path):
        """Should return False when the file does not exist (OSError)."""
        service = AnkiService(test_config)
        nonexistent = tmp_path / "missing.jpg"

        result = service.store_media_file("missing.jpg", nonexistent)

        assert result is False

    def test_correct_json_payload(self, test_config, tmp_path):
        """Should send correctly structured JSON to AnkiConnect."""
        service = AnkiService(test_config)
        filepath = tmp_path / "audio.mp3"
        filepath.write_bytes(b"mp3-content")

        resp = _mock_response(result="audio.mp3")

        with patch("requests.post", return_value=resp) as mock_post:
            service.store_media_file("my_audio.mp3", filepath)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == test_config.ankiconnect_url
        payload = call_kwargs[1]["json"]
        assert payload["action"] == "storeMediaFile"
        assert payload["version"] == 6
        assert "filename" in payload["params"]
        assert "data" in payload["params"]
        assert payload["params"]["filename"] == "my_audio.mp3"


# ---------------------------------------------------------------------------
# TestCreateCard
# ---------------------------------------------------------------------------


class TestCreateCard:
    """Tests for AnkiService.create_card."""

    def test_full_media_verifies_field_mapping(
        self, test_config, make_tokenized_word, make_media_data
    ):
        """Should create a card with screenshot and audio fields populated."""
        service = AnkiService(test_config)
        word = make_tokenized_word(lemma="飲む", sentence="水を飲む。")
        media = make_media_data(screenshot=True, audio=True, create_files=True)

        # store_media_file calls + addNote call
        store_resp = _mock_response(result="ok")
        add_resp = _mock_response(result=12345)

        with patch("requests.post", return_value=store_resp) as mock_post:
            # Override the last call to return addNote response
            mock_post.side_effect = [store_resp, store_resp, add_resp]
            result = service.create_card(word, media, "<b>to drink</b>")

        assert result is True

        # The third call should be the addNote
        add_call = mock_post.call_args_list[2]
        payload = add_call[1]["json"]
        note = payload["params"]["note"]

        assert note["deckName"] == "test_deck"
        assert note["modelName"] == "test_note_type"
        assert note["fields"]["word"] == "飲む"
        assert note["fields"]["sentence"] == "水を飲む。"
        assert note["fields"]["definition"] == "<b>to drink</b>"
        assert "img src" in note["fields"]["picture"]
        assert media.screenshot_filename in note["fields"]["picture"]
        assert "[sound:" in note["fields"]["audio"]
        assert media.audio_filename in note["fields"]["audio"]
        assert note["tags"] == ["auto-mined"]

    def test_no_media(self, test_config, make_tokenized_word):
        """Should create a card with empty picture and audio fields when no media."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()  # no paths, no filenames

        resp = _mock_response(result=99999)

        with patch("requests.post", return_value=resp) as mock_post:
            result = service.create_card(word, media, "a definition")

        assert result is True

        # Only one call (addNote), no store_media_file calls
        assert mock_post.call_count == 1
        payload = mock_post.call_args[1]["json"]
        note = payload["params"]["note"]
        assert note["fields"]["picture"] == ""
        assert note["fields"]["audio"] == ""

    def test_no_definition_defaults_to_empty_string(self, test_config, make_tokenized_word):
        """Should use empty string when definition is None."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=11111)

        with patch("requests.post", return_value=resp) as mock_post:
            result = service.create_card(word, media, None)

        assert result is True
        payload = mock_post.call_args[1]["json"]
        assert payload["params"]["note"]["fields"]["definition"] == ""

    def test_anki_error_response_returns_false(self, test_config, make_tokenized_word):
        """Should return False when addNote returns an error."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(error="duplicate note")

        with patch("requests.post", return_value=resp):
            result = service.create_card(word, media, "definition")

        assert result is False

    def test_request_exception_returns_false(self, test_config, make_tokenized_word):
        """Should return False on RequestException during addNote."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        with patch("requests.post", side_effect=requests.exceptions.Timeout()):
            result = service.create_card(word, media, "definition")

        assert result is False

    def test_field_mapping_matches_config(self, test_config, make_tokenized_word):
        """Should use field names from config.anki_fields for the note."""
        service = AnkiService(test_config)
        word = make_tokenized_word(lemma="走る", sentence="公園を走る。")
        media = MediaData(
            screenshot_filename="shot.jpg",
            audio_filename="clip.mp3",
        )

        resp = _mock_response(result=55555)

        with patch("requests.post", return_value=resp) as mock_post:
            service.create_card(word, media, "to run")

        payload = mock_post.call_args[1]["json"]
        note_fields = payload["params"]["note"]["fields"]

        # Keys should match the config field names exactly
        assert set(note_fields.keys()) == {
            test_config.anki_fields["word"],
            test_config.anki_fields["sentence"],
            test_config.anki_fields["definition"],
            test_config.anki_fields["picture"],
            test_config.anki_fields["audio"],
            test_config.anki_fields["expression_furigana"],
            test_config.anki_fields["sentence_furigana"],
        }


# ---------------------------------------------------------------------------
# TestCreateCardsBatch
# ---------------------------------------------------------------------------


class TestCreateCardsBatch:
    """Tests for AnkiService.create_cards_batch."""

    def _make_word_data(self, make_tokenized_word, n=1, prefix="word"):
        """Helper to create a list of (word, media, definition) tuples."""
        items = []
        for i in range(n):
            word = make_tokenized_word(lemma=f"{prefix}_{i}")
            media = MediaData()  # no files to avoid store_media_file IO
            items.append((word, media, f"def_{i}"))
        return items

    def test_empty_list_returns_zero(self, test_config):
        """Should return 0 immediately for an empty list."""
        service = AnkiService(test_config)

        result = service.create_cards_batch([])

        assert result == 0

    def test_single_batch_under_fifty(self, test_config, make_tokenized_word, recording_progress):
        """Should process all items in one batch when count < 50."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        resp = _mock_response(result=[100, 101, 102])

        with patch("requests.post", return_value=resp):
            result = service.create_cards_batch(items, recording_progress)

        assert result == 3

    def test_multiple_batches_seventy_five_items(
        self, test_config, make_tokenized_word, recording_progress
    ):
        """Should split 75 items into two batches (50 + 25) and sum results."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=75)

        # First batch: 50 items, all succeed
        batch1_resp = _mock_response(result=list(range(50)))
        # Second batch: 25 items, all succeed
        batch2_resp = _mock_response(result=list(range(50, 75)))

        with patch("requests.post", side_effect=[batch1_resp, batch2_resp]) as mock_post:
            result = service.create_cards_batch(items, recording_progress)

        assert result == 75
        # Exactly 2 batches (50 + 25), not more
        assert mock_post.call_count == 2

    def test_counts_only_non_null_note_ids(self, test_config, make_tokenized_word):
        """Should only count non-null IDs in the result array."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=5)

        # 3 out of 5 succeed (2 are null / duplicates)
        resp = _mock_response(result=[100, None, 102, None, 104])

        with patch("requests.post", return_value=resp):
            result = service.create_cards_batch(items)

        assert result == 3

    def test_progress_callback_lifecycle(
        self, test_config, make_tokenized_word, recording_progress
    ):
        """Should call on_start, on_progress, and on_complete in order."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        resp = _mock_response(result=[1, 2, 3])

        with patch("requests.post", return_value=resp):
            service.create_cards_batch(items, recording_progress)

        # on_start called once with total count
        assert len(recording_progress.starts) == 1
        assert recording_progress.starts[0][0] == 3
        assert "Creating Anki cards" in recording_progress.starts[0][1]

        # on_progress called once (one batch)
        assert len(recording_progress.progresses) == 1

        # on_complete called once
        assert recording_progress.completes == 1

        # no errors
        assert len(recording_progress.errors) == 0

    def test_batch_error_in_response(self, test_config, make_tokenized_word, recording_progress):
        """Should report error via callback when batch returns error."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        resp = _mock_response(error="deck not found")

        with patch("requests.post", return_value=resp):
            result = service.create_cards_batch(items, recording_progress)

        assert result == 0
        assert len(recording_progress.errors) == 1
        assert "Batch 1" in recording_progress.errors[0][0]
        assert "deck not found" in recording_progress.errors[0][1]

    def test_request_exception_handling(self, test_config, make_tokenized_word, recording_progress):
        """Should catch exceptions during batch request and report via callback."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        with patch(
            "requests.post",
            side_effect=requests.exceptions.ConnectionError("network down"),
        ):
            result = service.create_cards_batch(items, recording_progress)

        assert result == 0
        assert len(recording_progress.errors) == 1
        assert "Batch 1" in recording_progress.errors[0][0]
        # on_complete should still be called
        assert recording_progress.completes == 1


# ---------------------------------------------------------------------------
# TestStoreMediaFilesBatch
# ---------------------------------------------------------------------------


class TestStoreMediaFilesBatch:
    """Tests for AnkiService._store_media_files_batch."""

    def test_stores_both_screenshot_and_audio(self, test_config, make_tokenized_word, tmp_path):
        """Should send storeMediaFile for both screenshot and audio when paths exist."""
        service = AnkiService(test_config)

        word = make_tokenized_word()
        ss_path = tmp_path / "shot.jpg"
        ss_path.write_bytes(b"screenshot-data")
        au_path = tmp_path / "clip.mp3"
        au_path.write_bytes(b"audio-data")

        media = MediaData(
            screenshot_path=ss_path,
            audio_path=au_path,
            screenshot_filename="shot.jpg",
            audio_filename="clip.mp3",
        )

        resp = _mock_response(result="ok")

        with patch("requests.post", return_value=resp) as mock_post:
            service._store_media_files_batch([(word, media, "def")])

        # Two calls: one for screenshot, one for audio
        assert mock_post.call_count == 2

        filenames_sent = [
            call[1]["json"]["params"]["filename"] for call in mock_post.call_args_list
        ]
        assert "shot.jpg" in filenames_sent
        assert "clip.mp3" in filenames_sent

    def test_skips_nonexistent_paths(self, test_config, make_tokenized_word, tmp_path):
        """Should not attempt to store files when paths do not exist on disk."""
        service = AnkiService(test_config)

        word = make_tokenized_word()
        # Paths set but files not created on disk
        media = MediaData(
            screenshot_path=tmp_path / "missing.jpg",
            audio_path=tmp_path / "missing.mp3",
            screenshot_filename="missing.jpg",
            audio_filename="missing.mp3",
        )

        resp = _mock_response(result="ok")

        with patch("requests.post", return_value=resp) as mock_post:
            service._store_media_files_batch([(word, media, "def")])

        # No calls because files don't exist
        mock_post.assert_not_called()

    def test_silently_handles_errors(self, test_config, make_tokenized_word, tmp_path):
        """Should swallow exceptions and continue without raising."""
        service = AnkiService(test_config)

        word = make_tokenized_word()
        ss_path = tmp_path / "shot.jpg"
        ss_path.write_bytes(b"data")

        media = MediaData(
            screenshot_path=ss_path,
            screenshot_filename="shot.jpg",
        )

        with patch(
            "requests.post",
            side_effect=requests.exceptions.ConnectionError("fail"),
        ):
            # Should not raise
            service._store_media_files_batch([(word, media, "def")])


# ---------------------------------------------------------------------------
# TestOptionalFields
# ---------------------------------------------------------------------------


class TestOptionalFields:
    """Tests for optional field handling (pitch_accent, frequency_rank)."""

    def test_create_card_with_extra_fields(self, test_config, make_tokenized_word):
        """Should include mapped optional fields in the note payload."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=12345)

        with patch("requests.post", return_value=resp) as mock_post:
            result = service.create_card(
                word,
                media,
                "definition",
                extra_fields={"pitch_accent": "0", "frequency_rank": "500"},
            )

        assert result is True
        payload = mock_post.call_args[1]["json"]
        note_fields = payload["params"]["note"]["fields"]
        assert note_fields["PitchAccent"] == "0"
        assert note_fields["FrequencyRank"] == "500"

    def test_create_card_extra_fields_skipped_when_not_mapped(self, temp_dir):
        """Should not include optional fields when config maps them to empty string."""
        from anki_miner.config import AnkiMinerConfig

        config = AnkiMinerConfig(
            anki_fields={
                "word": "word",
                "sentence": "sentence",
                "definition": "definition",
                "picture": "picture",
                "audio": "audio",
                "expression_furigana": "expression_furigana",
                "sentence_furigana": "sentence_furigana",
                "pitch_accent": "",  # Not mapped
                "frequency_rank": "",  # Not mapped
            },
            media_temp_folder=temp_dir / "temp",
            jmdict_path=temp_dir / "dict",
        )
        service = AnkiService(config)
        word = make_word_helper()
        media = MediaData()

        resp = _mock_response(result=12345)

        with patch("requests.post", return_value=resp) as mock_post:
            service.create_card(
                word,
                media,
                "definition",
                extra_fields={"pitch_accent": "0", "frequency_rank": "500"},
            )

        payload = mock_post.call_args[1]["json"]
        note_fields = payload["params"]["note"]["fields"]
        # Empty-mapped fields should NOT appear
        assert "PitchAccent" not in note_fields
        assert "FrequencyRank" not in note_fields
        assert "" not in note_fields

    def test_create_card_ignores_unknown_extra_keys(self, test_config, make_tokenized_word):
        """Should silently ignore extra_fields keys not in OPTIONAL_FIELD_KEYS."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=12345)

        with patch("requests.post", return_value=resp) as mock_post:
            service.create_card(
                word,
                media,
                "definition",
                extra_fields={"unknown_key": "some_value"},
            )

        payload = mock_post.call_args[1]["json"]
        note_fields = payload["params"]["note"]["fields"]
        assert "some_value" not in note_fields.values()

    def test_create_card_no_extra_fields(self, test_config, make_tokenized_word):
        """Should work normally when extra_fields is None."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=12345)

        with patch("requests.post", return_value=resp) as mock_post:
            result = service.create_card(word, media, "definition", extra_fields=None)

        assert result is True
        payload = mock_post.call_args[1]["json"]
        note_fields = payload["params"]["note"]["fields"]
        # Optional fields should not appear when extra_fields is None
        assert "PitchAccent" not in note_fields
        assert "FrequencyRank" not in note_fields

    def test_batch_with_4_tuples_and_extra_fields(self, test_config, make_tokenized_word):
        """Should include optional fields when batch items are 4-tuples."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()
        extra = {"pitch_accent": "1", "frequency_rank": "200"}

        resp = _mock_response(result=[12345])

        with patch("requests.post", return_value=resp) as mock_post:
            result = service.create_cards_batch([(word, media, "definition", extra)])

        assert result == 1
        payload = mock_post.call_args[1]["json"]
        note = payload["params"]["notes"][0]
        assert note["fields"]["PitchAccent"] == "1"
        assert note["fields"]["FrequencyRank"] == "200"


def make_word_helper():
    """Standalone helper to create a TokenizedWord without fixtures."""
    from anki_miner.models import TokenizedWord

    return TokenizedWord(
        surface="食べる",
        lemma="食べる",
        reading="タベル",
        sentence="日本語を食べる。",
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
    )

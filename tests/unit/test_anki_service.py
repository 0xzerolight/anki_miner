"""Tests for anki_service module."""

import base64
import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.models import CardPayload, MediaData
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


class TestInit:
    """Tests for AnkiService initialization."""

    def test_missing_required_fields_raises_valueerror(self, test_config):
        """Should raise ValueError when required anki_fields keys are missing."""
        from dataclasses import replace

        bad_config = replace(test_config, anki_fields={"word": "word"})  # Missing many keys
        with pytest.raises(ValueError, match="Missing required anki_fields keys"):
            AnkiService(bad_config)


class TestStripForDedup:
    """Tests for the _strip_for_dedup helper (Anki dedup-key alignment)."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("食べる", "食べる"),
            ("<b>食べる</b>", "食べる"),
            ("<div>食べる</div>", "食べる"),
            ('<span style="x">食べる</span>', "食べる"),
            ("食べる&nbsp;", "食べる"),
            ("A&amp;B", "A&B"),
            ("[sound:foo.mp3]食べる", "食べる"),
            ("  食べる  ", "食べる"),
        ],
    )
    def test_normalizes_to_plain_key(self, raw, expected):
        """HTML, media refs, entities, and whitespace collapse to the bare value."""
        from anki_miner.services.anki_service import _strip_for_dedup

        assert _strip_for_dedup(raw) == expected

    def test_reading_furigana_brackets_preserved(self):
        """Anki does NOT strip [reading] furigana, so neither do we — stays distinct."""
        from anki_miner.services.anki_service import _strip_for_dedup

        assert _strip_for_dedup("食べる[たべる]") == "食べる[たべる]"
        assert _strip_for_dedup("食べる[たべる]") != _strip_for_dedup("食べる")


class TestGetExistingVocabulary:
    """Tests for AnkiService.get_existing_vocabulary."""

    def test_strips_markup_from_first_field(self, test_config):
        """A markup-wrapped Expression must reduce to the plain word so the
        filter matches it (otherwise it slips through and collides on add)."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1, 2])
        notes_resp = _mock_response(
            result=[
                {"fields": {"word": {"value": "<b>食べる</b>"}}},
                {"fields": {"word": {"value": "[sound:a.mp3]飲む"}}},
            ]
        )

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"食べる", "飲む"}

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

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"食べる", "飲む", "走る"}

    def test_empty_collection(self, test_config):
        """Should return empty set when no note IDs are returned."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=find_resp):
            result = service.get_existing_vocabulary()

        assert result == set()

    def test_find_notes_error_response(self, test_config):
        """Should raise AnkiConnectionError when findNotes returns an error."""
        service = AnkiService(test_config)

        find_resp = _mock_response(error="Invalid query")

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=find_resp),
            pytest.raises(AnkiConnectionError),
        ):
            service.get_existing_vocabulary()

    def test_notes_info_error_response(self, test_config):
        """Should raise AnkiConnectionError when notesInfo returns an error."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1, 2])
        notes_resp = _mock_response(error="Something went wrong")

        with (
            patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]),
            pytest.raises(AnkiConnectionError),
        ):
            service.get_existing_vocabulary()

    def test_connection_error_raises_anki_connection_error(self, test_config):
        """Should raise AnkiConnectionError on ConnectionError."""
        service = AnkiService(test_config)

        with (
            patch("anki_miner.services._ankiconnect.requests.post", side_effect=requests.exceptions.ConnectionError()),
            pytest.raises(AnkiConnectionError, match="Cannot connect"),
        ):
            service.get_existing_vocabulary()

    def test_request_exception_returns_empty_set(self, test_config):
        """Should return empty set on generic RequestException."""
        service = AnkiService(test_config)

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=requests.exceptions.Timeout()):
            result = service.get_existing_vocabulary()

        assert result == set()

    def test_value_error_returns_empty_set(self, test_config):
        """Should return empty set on ValueError (e.g., bad JSON)."""
        service = AnkiService(test_config)

        bad_resp = MagicMock()
        bad_resp.json.side_effect = ValueError("No JSON")

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=bad_resp):
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

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
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

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"飲む"}

    def test_queries_all_decks(self, test_config):
        """Should query deck:* to find notes across all decks."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1])
        notes_resp = _mock_response(result=[{"fields": {"Expression": {"value": "見る"}}}])

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]) as mock_post:
            result = service.get_existing_vocabulary()

        find_call_payload = mock_post.call_args_list[0][1]["json"]
        assert find_call_payload["params"]["query"] == "deck:*"
        assert result == {"見る"}

    def test_extracts_first_field_from_any_note_type(self, test_config):
        """Should extract the first field regardless of field name."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1, 2, 3])
        notes_resp = _mock_response(
            result=[
                # Lapis note type
                {"fields": {"Expression": {"value": "食べる"}, "Sentence": {"value": "..."}}},
                # Core 2k note type
                {"fields": {"Vocabulary-Kanji": {"value": "飲む"}, "Reading": {"value": "..."}}},
                # Custom note type
                {"fields": {"Front": {"value": "走る"}, "Back": {"value": "..."}}},
            ]
        )

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"食べる", "飲む", "走る"}

    def test_filters_non_japanese_words(self, test_config):
        """Should exclude words that contain no Japanese characters."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1, 2, 3, 4])
        notes_resp = _mock_response(
            result=[
                {"fields": {"Word": {"value": "食べる"}}},
                {"fields": {"Front": {"value": "hello"}}},  # English
                {"fields": {"Vocab": {"value": "카페"}}},  # Korean
                {"fields": {"Expression": {"value": "日本語"}}},
            ]
        )

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"食べる", "日本語"}

    def test_logs_warning_when_no_notes_found(self, test_config, caplog):
        """Should log a warning when findNotes returns empty result."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[])

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=find_resp),
            caplog.at_level(logging.WARNING),
        ):
            result = service.get_existing_vocabulary()

        assert result == set()
        assert "No notes found in Anki collection" in caplog.text

    def test_logs_warning_on_request_exception(self, test_config, caplog):
        """Should log a warning when a RequestException is caught."""
        service = AnkiService(test_config)

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post", side_effect=requests.exceptions.Timeout("timed out")
            ),
            caplog.at_level(logging.WARNING),
        ):
            result = service.get_existing_vocabulary()

        assert result == set()
        assert "Failed to fetch existing vocabulary" in caplog.text

    def test_batches_large_note_collections(self, test_config):
        """Should batch notesInfo requests for large collections."""
        service = AnkiService(test_config)

        # 2500 note IDs → 3 batches (1000 + 1000 + 500)
        note_ids = list(range(1, 2501))
        find_resp = _mock_response(result=note_ids)

        batch1_resp = _mock_response(result=[{"fields": {"word": {"value": f"語{i}"}}} for i in range(1000)])
        batch2_resp = _mock_response(result=[{"fields": {"word": {"value": f"語{i}"}}} for i in range(1000, 2000)])
        batch3_resp = _mock_response(result=[{"fields": {"word": {"value": f"語{i}"}}} for i in range(2000, 2500)])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[find_resp, batch1_resp, batch2_resp, batch3_resp],
        ) as mock_post:
            result = service.get_existing_vocabulary()

        # 1 findNotes + 3 notesInfo batches = 4 calls
        assert mock_post.call_count == 4
        assert len(result) == 2500

        # Verify batch sizes
        for call_idx in [1, 2, 3]:
            payload = mock_post.call_args_list[call_idx][1]["json"]
            assert payload["action"] == "notesInfo"

        # First batch: 1000 notes
        assert len(mock_post.call_args_list[1][1]["json"]["params"]["notes"]) == 1000
        # Last batch: 500 notes
        assert len(mock_post.call_args_list[3][1]["json"]["params"]["notes"]) == 500


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

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
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

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.store_media_file("test.jpg", filepath)

        assert result is False

    def test_request_exception_returns_false(self, test_config, tmp_path):
        """Should return False on RequestException."""
        service = AnkiService(test_config)
        filepath = tmp_path / "test.jpg"
        filepath.write_bytes(b"data")

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=requests.exceptions.ConnectionError()):
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

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
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
# TestCreateCardsBatch
# ---------------------------------------------------------------------------


class TestCreateCardsBatch:
    """Tests for AnkiService.create_cards_batch."""

    def _make_word_data(self, make_tokenized_word, n=1, prefix="word"):
        """Helper to create a list of CardPayload objects."""
        items = []
        for i in range(n):
            word = make_tokenized_word(lemma=f"{prefix}_{i}")
            media = MediaData()  # no files to avoid store_media_file IO
            items.append(CardPayload(word=word, media=media, definition=f"def_{i}"))
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

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.create_cards_batch(items, recording_progress)

        assert result == 3

    def test_multiple_batches_one_fifty_items(self, test_config, make_tokenized_word, recording_progress):
        """Should split 150 items into two batches (100 + 50) and sum results."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=150)

        # First batch: 100 items, all succeed
        batch1_resp = _mock_response(result=list(range(100)))
        # Second batch: 50 items, all succeed
        batch2_resp = _mock_response(result=list(range(100, 150)))

        with patch(
            "anki_miner.services._ankiconnect.requests.post", side_effect=[batch1_resp, batch2_resp]
        ) as mock_post:
            result = service.create_cards_batch(items, recording_progress)

        assert result == 150
        # Exactly 2 batches (100 + 50), not more
        assert mock_post.call_count == 2

    def test_counts_only_non_null_note_ids(self, test_config, make_tokenized_word):
        """Should only count non-null IDs in the result array."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=5)

        # 3 out of 5 succeed (2 are null / duplicates)
        resp = _mock_response(result=[100, None, 102, None, 104])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.create_cards_batch(items)

        assert result == 3

    def test_top_level_duplicate_error_recovers_per_note(self, test_config, make_tokenized_word):
        """A top-level duplicate error must not abort the run: retry per-note,
        skip duplicates, create the rest, and report the skipped count."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        # addNotes raises a top-level duplicate error; then per-note addNote:
        # note 0 ok, note 1 duplicate (skipped), note 2 ok.
        batch_dup = _mock_response(error=["cannot create note because it is a duplicate"])
        note0 = _mock_response(result=100)
        note1_dup = _mock_response(error="cannot create note because it is a duplicate")
        note2 = _mock_response(result=102)

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[batch_dup, note0, note1_dup, note2],
        ):
            result = service.create_cards_batch(items)

        assert result == 2
        assert service.last_skipped_duplicates == 1
        assert service.last_created_note_ids == [100, 102]

    def test_non_duplicate_batch_error_propagates(self, test_config, make_tokenized_word):
        """A non-duplicate addNotes error (e.g. missing deck) still aborts."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=2)

        err_resp = _mock_response(error="deck was not found: Anki Miner")

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=err_resp),
            pytest.raises(AnkiConnectionError, match="deck was not found"),
        ):
            service.create_cards_batch(items)

    def test_non_duplicate_error_during_per_note_fallback_propagates(self, test_config, make_tokenized_word):
        """If a non-duplicate error surfaces while recovering per-note, raise it."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=2)

        batch_dup = _mock_response(error=["cannot create note because it is a duplicate"])
        note0 = _mock_response(result=100)
        note1_fatal = _mock_response(error="model was not found: Lapis")

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=[batch_dup, note0, note1_fatal],
            ),
            pytest.raises(AnkiConnectionError, match="model was not found"),
        ):
            service.create_cards_batch(items)

    def test_progress_callback_lifecycle(self, test_config, make_tokenized_word, recording_progress):
        """Should call on_start, on_progress, and on_complete in order."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        resp = _mock_response(result=[1, 2, 3])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
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

    def test_batch_error_in_response_propagates(self, test_config, make_tokenized_word, recording_progress):
        """AnkiConnect error payloads now propagate as AnkiConnectionError.

        Pre-T2.3 the batch path swallowed errors and reported them via the
        progress callback. The unified ``post_action`` helper raises, and
        callers catch at the pipeline boundary instead.
        """
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        resp = _mock_response(error="deck not found")

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            pytest.raises(AnkiConnectionError, match="deck not found"),
        ):
            service.create_cards_batch(items, recording_progress)

    def test_request_exception_propagates(self, test_config, make_tokenized_word, recording_progress):
        """Connection errors now propagate as AnkiConnectionError."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ConnectionError("network down"),
            ),
            pytest.raises(AnkiConnectionError, match="Cannot connect"),
        ):
            service.create_cards_batch(items, recording_progress)

    def test_create_cards_batch_uses_surface_for_noun_expression(self, test_config, make_tokenized_word):
        """Nouns mine as surface (Issue #5: unidic 豪腕 → 剛腕 mis-lemma)."""
        service = AnkiService(test_config)
        word = make_tokenized_word(surface="豪腕", lemma="剛腕", sentence="豪腕の男だ。", pos="名詞")
        media = MediaData()

        resp = _mock_response(result=[12345])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            result = service.create_cards_batch([CardPayload(word=word, media=media, definition="definition")])

        assert result == 1
        payload = mock_post.call_args[1]["json"]
        note = payload["params"]["notes"][0]
        word_field_name = test_config.anki_fields["word"]
        assert note["fields"][word_field_name] == "豪腕"

    def test_create_cards_batch_uses_lemma_for_verb_expression(self, test_config, make_tokenized_word):
        """Verbs mine as lemma (Issue #19: 破れ surface → 破れる dictionary form)."""
        service = AnkiService(test_config)
        word = make_tokenized_word(surface="破れ", lemma="破れる", sentence="胸のとこ破れそう。", pos="動詞")
        media = MediaData()

        resp = _mock_response(result=[12346])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            result = service.create_cards_batch([CardPayload(word=word, media=media, definition="definition")])

        assert result == 1
        payload = mock_post.call_args[1]["json"]
        note = payload["params"]["notes"][0]
        word_field_name = test_config.anki_fields["word"]
        assert note["fields"][word_field_name] == "破れる"

    def test_allow_duplicate_cards_adds_options(self, test_config, make_tokenized_word):
        """allow_duplicate_cards=True -> each note carries the AnkiConnect dup options."""
        import dataclasses as _dc

        config = _dc.replace(test_config, allow_duplicate_cards=True)
        service = AnkiService(config)
        word = make_tokenized_word(surface="猫", lemma="猫", sentence="猫だ。", pos="名詞")
        media = MediaData()
        resp = _mock_response(result=[1])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition="d")])

        note = mock_post.call_args[1]["json"]["params"]["notes"][0]
        assert note["options"] == {"allowDuplicate": True, "duplicateScope": "deck"}

    def test_no_options_when_allow_duplicate_cards_off(self, test_config, make_tokenized_word):
        """Default (allow_duplicate_cards=False) -> no options key, normal dedup."""
        service = AnkiService(test_config)
        word = make_tokenized_word(surface="猫", lemma="猫", sentence="猫だ。", pos="名詞")
        media = MediaData()
        resp = _mock_response(result=[1])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition="d")])

        note = mock_post.call_args[1]["json"]["params"]["notes"][0]
        assert "options" not in note

    def test_bolded_sentence_used_when_flag_on(self, test_config, make_tokenized_word):
        """When bold_target_in_sentence=True and precomputed forms exist, the
        Sentence and SentenceFurigana fields use those (Issue #20)."""
        import dataclasses as _dc

        config = _dc.replace(test_config, bold_target_in_sentence=True)
        service = AnkiService(config)
        word = make_tokenized_word(
            surface="食べる",
            lemma="食べる",
            sentence="毎日食べる",
            sentence_furigana="毎日 食べる[たべる]",
            pos="動詞",
        )
        word.sentence_bolded = "毎日<b>食べる</b>"
        word.sentence_furigana_bolded = "毎日 <b>食べる[たべる]</b>"
        media = MediaData()
        resp = _mock_response(result=[1])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition="def")])

        note = mock_post.call_args[1]["json"]["params"]["notes"][0]
        sentence_field = config.anki_fields["sentence"]
        furi_field = config.anki_fields["sentence_furigana"]
        assert note["fields"][sentence_field] == "毎日<b>食べる</b>"
        assert note["fields"][furi_field] == "毎日 <b>食べる[たべる]</b>"

    def test_sentence_escape_path_when_flag_off(self, test_config, make_tokenized_word):
        """Flag off: even if precomputed strings exist, fall back to plain escape.

        Regression guard: existing cards must keep current behavior.
        """
        service = AnkiService(test_config)
        word = make_tokenized_word(sentence="A & B", sentence_furigana="A & B")
        word.sentence_bolded = "A <b>&amp;</b> B"  # should NOT be used
        media = MediaData()
        resp = _mock_response(result=[1])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition="d")])

        note = mock_post.call_args[1]["json"]["params"]["notes"][0]
        sentence_field = test_config.anki_fields["sentence"]
        assert note["fields"][sentence_field] == "A &amp; B"

    def test_bolded_falls_back_when_precomputed_empty(self, test_config, make_tokenized_word):
        """Flag on but precomputed string empty → escape fallback (defensive)."""
        import dataclasses as _dc

        config = _dc.replace(test_config, bold_target_in_sentence=True)
        service = AnkiService(config)
        word = make_tokenized_word(sentence="A & B")
        # sentence_bolded left empty intentionally
        media = MediaData()
        resp = _mock_response(result=[1])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition="d")])

        note = mock_post.call_args[1]["json"]["params"]["notes"][0]
        sentence_field = config.anki_fields["sentence"]
        assert note["fields"][sentence_field] == "A &amp; B"


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

        # multi response: two sub-results (one per file)
        resp = _mock_response(result=["shot.jpg", "clip.mp3"])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            stored = service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

        # One batched POST via multi action
        assert mock_post.call_count == 1
        payload = mock_post.call_args[1]["json"]
        assert payload["action"] == "multi"

        filenames_sent = [a["params"]["filename"] for a in payload["params"]["actions"]]
        assert "shot.jpg" in filenames_sent
        assert "clip.mp3" in filenames_sent
        assert stored == {"shot.jpg", "clip.mp3"}

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

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

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
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=requests.exceptions.ConnectionError("fail"),
        ):
            # Should not raise
            service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

    def test_store_media_files_uses_multi_action(self, test_config, make_tokenized_word, tmp_path):
        """Should POST a single multi action for all files, returning all filenames in stored."""
        service = AnkiService(test_config)

        items = []
        for i in range(3):
            word = make_tokenized_word(lemma=f"word_{i}")
            ss_path = tmp_path / f"shot_{i}.jpg"
            ss_path.write_bytes(b"screenshot-data")
            au_path = tmp_path / f"clip_{i}.mp3"
            au_path.write_bytes(b"audio-data")
            media = MediaData(
                screenshot_path=ss_path,
                audio_path=au_path,
                screenshot_filename=f"shot_{i}.jpg",
                audio_filename=f"clip_{i}.mp3",
            )
            items.append(CardPayload(word=word, media=media, definition=f"def_{i}"))

        # 3 cards × 2 files = 6 sub-results (all successful: no error key)
        multi_resp = _mock_response(
            result=["shot_0.jpg", "shot_1.jpg", "shot_2.jpg", "clip_0.mp3", "clip_1.mp3", "clip_2.mp3"]
        )

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=multi_resp) as mock_post:
            stored = service._store_media_files_batch(items)

        # One POST (all 6 files fit in a single chunk of ≤50)
        assert mock_post.call_count == 1
        payload = mock_post.call_args[1]["json"]
        assert payload["action"] == "multi"
        assert len(payload["params"]["actions"]) == 6

        # All filenames returned in stored set
        expected = {f"shot_{i}.jpg" for i in range(3)} | {f"clip_{i}.mp3" for i in range(3)}
        assert stored == expected

    def test_store_media_partial_failure_excludes_failed_filename(self, test_config, make_tokenized_word, tmp_path):
        """A sub-result with an error key should exclude that filename from stored."""
        service = AnkiService(test_config)

        word = make_tokenized_word()
        ss_path = tmp_path / "shot.jpg"
        ss_path.write_bytes(b"screenshot-data")
        au_path = tmp_path / "clip.mp3"
        au_path.write_bytes(b"audio-data")
        bad_path = tmp_path / "bad.jpg"
        bad_path.write_bytes(b"bad-data")

        items = [
            CardPayload(
                word=word,
                media=MediaData(
                    screenshot_path=ss_path,
                    audio_path=au_path,
                    screenshot_filename="shot.jpg",
                    audio_filename="clip.mp3",
                ),
                definition="def",
            ),
            CardPayload(
                word=make_tokenized_word(lemma="word2"),
                media=MediaData(
                    screenshot_path=bad_path,
                    screenshot_filename="bad.jpg",
                ),
                definition="def2",
            ),
        ]

        # sub-results: shot.jpg ok, clip.mp3 ok, bad.jpg has error
        multi_result = ["shot.jpg", "clip.mp3", {"error": "failed to store bad.jpg"}]
        multi_resp = _mock_response(result=multi_result)

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=multi_resp):
            stored = service._store_media_files_batch(items)

        assert "shot.jpg" in stored
        assert "clip.mp3" in stored
        assert "bad.jpg" not in stored

    def test_length_mismatch_logs_warning(self, test_config, make_tokenized_word, tmp_path, caplog):
        """A chunk where post_multi returns fewer results than actions should log a warning."""
        service = AnkiService(test_config)

        word = make_tokenized_word()
        ss_path = tmp_path / "shot.jpg"
        ss_path.write_bytes(b"data")
        au_path = tmp_path / "clip.mp3"
        au_path.write_bytes(b"data")

        media = MediaData(
            screenshot_path=ss_path,
            audio_path=au_path,
            screenshot_filename="shot.jpg",
            audio_filename="clip.mp3",
        )

        # Return only one result for two actions — deliberate mismatch
        resp = _mock_response(result=["shot.jpg"])

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            caplog.at_level(logging.WARNING, logger="anki_miner.services.anki_service"),
        ):
            stored = service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

        assert any("silently skipped" in r.message for r in caplog.records)
        # Only the one result that came back should be counted
        assert "shot.jpg" in stored
        assert "clip.mp3" not in stored


# ---------------------------------------------------------------------------
# TestPostMultiErrors
# ---------------------------------------------------------------------------


class TestPostMultiErrors:
    """Direct error-path tests for post_multi (mirrors post_action error tests)."""

    def test_top_level_error_raises_anki_connection_error(self):
        """A top-level ``{"error": ...}`` from the multi envelope should raise AnkiConnectionError."""
        from anki_miner.services._ankiconnect import post_multi

        resp = _mock_response(error="multi envelope rejected")
        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            pytest.raises(AnkiConnectionError, match="multi envelope rejected"),
        ):
            post_multi("http://localhost:8765", [{"action": "noop", "version": 6, "params": {}}])

    def test_connection_error_raises_with_is_anki_running_message(self):
        """A ``ConnectionError`` from requests should raise AnkiConnectionError with the standard message."""
        from anki_miner.services._ankiconnect import post_multi

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ConnectionError("refused"),
            ),
            pytest.raises(AnkiConnectionError, match="Is Anki running"),
        ):
            post_multi("http://localhost:8765", [{"action": "noop", "version": 6, "params": {}}])


# ---------------------------------------------------------------------------
# TestOptionalFields
# ---------------------------------------------------------------------------


class TestOptionalFields:
    """Tests for optional field handling (pitch_position, pitch_category, frequency)."""

    def test_batch_extra_fields_skipped_when_not_mapped(self, temp_dir, make_tokenized_word):
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
                "expression_reading": "",
                "sentence_furigana": "sentence_furigana",
                "sentence_reading": "",
                "pitch_position": "",  # Not mapped
                "pitch_category": "",  # Not mapped
                "frequency": "",  # Not mapped
            },
            media_temp_folder=temp_dir / "temp",
            jmdict_path=temp_dir / "dict",
        )
        service = AnkiService(config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[12345])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch(
                [
                    CardPayload(
                        word=word,
                        media=media,
                        definition="definition",
                        extra_fields={"pitch_position": "0", "pitch_category": "平板", "frequency": "500"},
                    )
                ]
            )

        payload = mock_post.call_args[1]["json"]
        note_fields = payload["params"]["notes"][0]["fields"]
        assert "PitchPosition" not in note_fields
        assert "PitchCategory" not in note_fields
        assert "Frequency" not in note_fields
        assert "" not in note_fields

    def test_batch_ignores_unknown_extra_keys(self, test_config, make_tokenized_word):
        """Should silently ignore extra_fields keys not in OPTIONAL_FIELD_KEYS."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[12345])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch(
                [
                    CardPayload(
                        word=word,
                        media=media,
                        definition="definition",
                        extra_fields={"unknown_key": "some_value"},
                    )
                ]
            )

        payload = mock_post.call_args[1]["json"]
        note_fields = payload["params"]["notes"][0]["fields"]
        assert "some_value" not in note_fields.values()

    def test_batch_with_extra_fields(self, test_config, make_tokenized_word):
        """Should include optional fields when CardPayload has extra_fields set."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()
        extra = {"pitch_position": "1", "pitch_category": "頭高", "frequency": "200"}

        resp = _mock_response(result=[12345])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            result = service.create_cards_batch(
                [CardPayload(word=word, media=media, definition="definition", extra_fields=extra)]
            )

        assert result == 1
        payload = mock_post.call_args[1]["json"]
        note = payload["params"]["notes"][0]
        assert note["fields"]["PitchPosition"] == "1"
        assert note["fields"]["PitchCategory"] == "頭高"
        assert note["fields"]["Frequency"] == "200"


# ---------------------------------------------------------------------------
# TestReadingFields (Issue #7: plain-kana reading fields)
# ---------------------------------------------------------------------------


class TestReadingFields:
    """Tests for expression_reading / sentence_reading field handling (Issue #7)."""

    def _config_with_reading_fields(self, temp_dir):
        """Build a config that maps both new reading keys to real Anki field names."""
        from anki_miner.config import AnkiMinerConfig

        return AnkiMinerConfig(
            anki_fields={
                "word": "Expression",
                "sentence": "Sentence",
                "definition": "Definition",
                "picture": "Picture",
                "audio": "Audio",
                "expression_furigana": "ExpressionFurigana",
                "expression_reading": "ExpressionReading",
                "sentence_furigana": "SentenceFurigana",
                "sentence_reading": "SentenceReading",
                "pitch_position": "",
                "pitch_category": "",
                "frequency": "",
            },
            media_temp_folder=temp_dir / "temp",
            jmdict_path=temp_dir / "dict",
        )

    def test_create_cards_batch_skips_reading_fields_when_unmapped(self, test_config, make_tokenized_word):
        """With the default test_config (empty reading mappings), reading fields are skipped."""
        service = AnkiService(test_config)
        word = make_tokenized_word(
            expression_reading="まだけ",
            sentence_reading="わたしはねこです。",
        )
        media = MediaData()

        resp = _mock_response(result=[12345])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition="definition")])

        payload = mock_post.call_args[1]["json"]
        note_fields = payload["params"]["notes"][0]["fields"]
        # Plain-kana values must not be smuggled in under any field name
        assert "まだけ" not in note_fields.values()
        assert "わたしはねこです。" not in note_fields.values()

    def test_create_cards_batch_includes_reading_fields_when_mapped(self, temp_dir, make_tokenized_word):
        """Batch path should mirror single-card behavior for reading fields."""
        config = self._config_with_reading_fields(temp_dir)
        service = AnkiService(config)
        word = make_tokenized_word(
            surface="真竹",
            sentence="真竹を見た。",
            expression_reading="まだけ",
            sentence_reading="まだけをみた。",
        )
        media = MediaData()

        resp = _mock_response(result=[55555])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition="definition")])

        payload = mock_post.call_args[1]["json"]
        note_fields = payload["params"]["notes"][0]["fields"]
        assert note_fields["ExpressionReading"] == "まだけ"
        assert note_fields["SentenceReading"] == "まだけをみた。"


# ---------------------------------------------------------------------------
# TestDeleteNotes
# ---------------------------------------------------------------------------


class TestDeleteNotes:
    """Tests for AnkiService.delete_notes."""

    def test_success_returns_count(self, test_config):
        """Should send deleteNotes request and return count of deleted notes."""
        service = AnkiService(test_config)
        resp = _mock_response(result=None)

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            result = service.delete_notes([100, 200, 300])

        assert result == 3
        payload = mock_post.call_args[1]["json"]
        assert payload["action"] == "deleteNotes"
        assert payload["version"] == 6
        assert payload["params"]["notes"] == [100, 200, 300]

    def test_empty_list_returns_zero(self, test_config):
        """Should return 0 immediately for an empty list."""
        service = AnkiService(test_config)
        result = service.delete_notes([])
        assert result == 0

    def test_anki_error_raises(self, test_config):
        """Should raise AnkiConnectionError when AnkiConnect reports an error."""
        service = AnkiService(test_config)
        resp = _mock_response(error="notes not found")

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            pytest.raises(AnkiConnectionError, match="notes not found"),
        ):
            service.delete_notes([100])

    def test_connection_error_raises(self, test_config):
        """Should raise AnkiConnectionError on ConnectionError."""
        service = AnkiService(test_config)

        with (
            patch("anki_miner.services._ankiconnect.requests.post", side_effect=requests.exceptions.ConnectionError()),
            pytest.raises(AnkiConnectionError, match="Cannot connect"),
        ):
            service.delete_notes([100])


# ---------------------------------------------------------------------------
# TestLastCreatedNoteIds
# ---------------------------------------------------------------------------


class TestLastCreatedNoteIds:
    """Tests for AnkiService.last_created_note_ids tracking."""

    def _make_word_data(self, make_tokenized_word, n=1):
        """Helper to create a list of CardPayload objects."""
        items = []
        for i in range(n):
            word = make_tokenized_word(lemma=f"word_{i}")
            media = MediaData()
            items.append(CardPayload(word=word, media=media, definition=f"def_{i}"))
        return items

    def test_batch_populates_last_created_note_ids(self, test_config, make_tokenized_word):
        """After create_cards_batch, last_created_note_ids should contain the IDs."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)
        resp = _mock_response(result=[100, 101, 102])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            service.create_cards_batch(items)

        assert service.last_created_note_ids == [100, 101, 102]

    def test_batch_resets_on_new_call(self, test_config, make_tokenized_word):
        """Calling create_cards_batch again should reset last_created_note_ids."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=2)

        resp1 = _mock_response(result=[100, 101])
        resp2 = _mock_response(result=[200])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp1):
            service.create_cards_batch(items)
        assert service.last_created_note_ids == [100, 101]

        items2 = self._make_word_data(make_tokenized_word, n=1)
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp2):
            service.create_cards_batch(items2)
        assert service.last_created_note_ids == [200]

    def test_null_ids_filtered_out(self, test_config, make_tokenized_word):
        """Null IDs (failed cards) should not appear in last_created_note_ids."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=5)
        resp = _mock_response(result=[100, None, 102, None, 104])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            service.create_cards_batch(items)

        assert service.last_created_note_ids == [100, 102, 104]

    def test_empty_list_resets(self, test_config):
        """Calling create_cards_batch with empty list resets last_created_note_ids."""
        service = AnkiService(test_config)
        service.last_created_note_ids = [999]  # Set some old value
        service.create_cards_batch([])
        assert service.last_created_note_ids == []

    def test_initialized_as_empty(self, test_config):
        """last_created_note_ids should be empty on service creation."""
        service = AnkiService(test_config)
        assert service.last_created_note_ids == []


# ---------------------------------------------------------------------------
# TestGetNoteTypeFields
# ---------------------------------------------------------------------------


class TestGetNoteTypeFields:
    """Tests for AnkiService.get_note_type_fields."""

    def test_success_returns_field_list(self, test_config):
        """Should return list of field names on success."""
        service = AnkiService(test_config)
        resp = _mock_response(result=["Expression", "Sentence", "Definition"])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.get_note_type_fields()

        assert result == ["Expression", "Sentence", "Definition"]

    def test_uses_config_note_type_by_default(self, test_config):
        """Should use config.anki_note_type when no model_name passed."""
        service = AnkiService(test_config)
        resp = _mock_response(result=["Field1"])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.get_note_type_fields()

        payload = mock_post.call_args[1]["json"]
        assert payload["params"]["modelName"] == test_config.anki_note_type

    def test_uses_explicit_model_name(self, test_config):
        """Should use explicit model_name when provided."""
        service = AnkiService(test_config)
        resp = _mock_response(result=["Field1"])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.get_note_type_fields("CustomNote")

        payload = mock_post.call_args[1]["json"]
        assert payload["params"]["modelName"] == "CustomNote"

    def test_error_returns_empty_list(self, test_config):
        """Should return empty list when AnkiConnect reports an error."""
        service = AnkiService(test_config)
        resp = _mock_response(error="model not found")

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.get_note_type_fields()

        assert result == []

    def test_connection_error_returns_empty_list(self, test_config):
        """Should return empty list on connection error."""
        service = AnkiService(test_config)

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=requests.exceptions.ConnectionError()):
            result = service.get_note_type_fields()

        assert result == []


# ---------------------------------------------------------------------------
# TestConfigurableFields
# ---------------------------------------------------------------------------


class TestConfigurableFields:
    """Tests for configurable card fields (empty field names skip the field)."""

    def _config_with_empty_fields(self, temp_dir, empty_keys):
        """Create a config where certain field mappings are empty strings."""
        from anki_miner.config import AnkiMinerConfig

        fields = {
            "word": "word",
            "sentence": "sentence",
            "definition": "definition",
            "picture": "picture",
            "audio": "audio",
            "expression_furigana": "expression_furigana",
            "expression_reading": "",
            "sentence_furigana": "sentence_furigana",
            "sentence_reading": "",
            "pitch_position": "",
            "pitch_category": "",
            "frequency": "",
        }
        for key in empty_keys:
            fields[key] = ""

        return AnkiMinerConfig(
            anki_fields=fields,
            media_temp_folder=temp_dir / "temp",
            jmdict_path=temp_dir / "dict",
        )

    def test_create_cards_batch_skips_empty_field(self, temp_dir, make_tokenized_word):
        """Batch creation should also skip empty-mapped fields."""
        config = self._config_with_empty_fields(temp_dir, ["sentence_furigana"])
        service = AnkiService(config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[12345])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition="def")])

        payload = mock_post.call_args[1]["json"]
        note_fields = payload["params"]["notes"][0]["fields"]
        assert "sentence_furigana" not in note_fields
        assert "word" in note_fields


# ---------------------------------------------------------------------------
# TestDictMediaUpload
# ---------------------------------------------------------------------------


class TestDictMediaUpload:
    """Yomitan monolingual dictionaries reference SVG/PNG assets relative to
    the dictionary zip. The renderer rewrites those to flat namespaced
    filenames and tags them with `class="anki-miner-dict-media"`. AnkiService
    must scan definition HTML for those markers, locate the file under
    ``config.dicts_root/<dict_id>/media/<flat>``, and ship the bytes to Anki
    via storeMediaFile.
    """

    def _make_config_with_dict_media(self, test_config, dicts_root, dict_id="test-dict"):
        from dataclasses import replace

        media_dir = dicts_root / dict_id / "media"
        media_dir.mkdir(parents=True)
        (media_dir / "svg-accent_X.svg").write_bytes(b"<svg/>")
        return replace(test_config, dicts_root=dicts_root)

    def test_upload_dict_media_reads_file_and_calls_storemediafile(self, test_config, temp_dir, make_tokenized_word):
        config = self._make_config_with_dict_media(test_config, temp_dir / "dicts")
        service = AnkiService(config)

        definition = '<div>ふ<img class="anki-miner-dict-media" ' 'src="test-dict__svg-accent_X.svg">そ</div>'
        word = make_tokenized_word()
        media = MediaData()

        store_resp = _mock_response(result=None)
        create_resp = _mock_response(result=[12345])

        with patch(
            "anki_miner.services._ankiconnect.requests.post", side_effect=[store_resp, create_resp]
        ) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])

        # First call is storeMediaFile for the dict asset
        store_call = mock_post.call_args_list[0]
        store_payload = store_call[1]["json"]
        assert store_payload["action"] == "storeMediaFile"
        assert store_payload["params"]["filename"] == "test-dict__svg-accent_X.svg"
        assert base64.b64decode(store_payload["params"]["data"]) == b"<svg/>"

    def test_uploaded_files_cached_across_calls(self, test_config, temp_dir, make_tokenized_word):
        """Same SVG referenced by many cards should upload once, not many times."""
        config = self._make_config_with_dict_media(test_config, temp_dir / "dicts")
        service = AnkiService(config)

        definition = '<img class="anki-miner-dict-media" src="test-dict__svg-accent_X.svg">'
        word = make_tokenized_word()
        media = MediaData()

        store_resp = _mock_response(result=None)
        create_resp = _mock_response(result=[12345])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[store_resp, create_resp, create_resp, create_resp],
        ) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])

        # Three card creations + exactly one media upload.
        store_calls = [c for c in mock_post.call_args_list if c[1]["json"]["action"] == "storeMediaFile"]
        assert len(store_calls) == 1

    def test_missing_file_on_disk_is_logged_and_cached(self, test_config, temp_dir, make_tokenized_word, caplog):
        from dataclasses import replace

        # dicts_root exists but the referenced file does not.
        (temp_dir / "dicts").mkdir()
        config = replace(test_config, dicts_root=temp_dir / "dicts")
        service = AnkiService(config)

        definition = '<img class="anki-miner-dict-media" src="nope__missing.svg">'
        word = make_tokenized_word()
        media = MediaData()
        create_resp = _mock_response(result=[12345])

        with (
            caplog.at_level(logging.WARNING),
            patch("anki_miner.services._ankiconnect.requests.post", return_value=create_resp) as mock_post,
        ):
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])

        # No storeMediaFile attempted; warning logged once (cached after first).
        store_calls = [c for c in mock_post.call_args_list if c[1]["json"]["action"] == "storeMediaFile"]
        assert len(store_calls) == 0
        assert sum("Dict media file missing" in r.message for r in caplog.records) == 1

    def test_traversal_src_rejected(self, test_config, temp_dir, make_tokenized_word):
        from dataclasses import replace

        (temp_dir / "dicts").mkdir()
        config = replace(test_config, dicts_root=temp_dir / "dicts")
        service = AnkiService(config)

        # Even if a malicious dict somehow emitted a traversal-style src, the
        # resolver must refuse to follow it out of dicts_root.
        definition = '<img class="anki-miner-dict-media" src="..__..__etc__passwd">'
        word = make_tokenized_word()
        media = MediaData()
        create_resp = _mock_response(result=[12345])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=create_resp) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])

        store_calls = [c for c in mock_post.call_args_list if c[1]["json"]["action"] == "storeMediaFile"]
        assert len(store_calls) == 0

    def test_batch_upload_collects_from_all_definitions(self, test_config, temp_dir, make_tokenized_word):
        config = self._make_config_with_dict_media(test_config, temp_dir / "dicts", dict_id="d1")
        # Add a second file referenced by a different card in the batch.
        (config.dicts_root / "d1" / "media" / "second.svg").write_bytes(b"<svg2/>")

        service = AnkiService(config)
        word = make_tokenized_word()
        media = MediaData()

        defs = [
            '<img class="anki-miner-dict-media" src="d1__svg-accent_X.svg">',
            '<img class="anki-miner-dict-media" src="d1__second.svg">',
            # Duplicate of the first — must not re-upload.
            '<img class="anki-miner-dict-media" src="d1__svg-accent_X.svg">',
        ]
        word_data_list = [CardPayload(word=word, media=media, definition=d) for d in defs]

        store_resp = _mock_response(result=None)
        create_resp = _mock_response(result=[1, 2, 3])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[store_resp, store_resp, create_resp],
        ) as mock_post:
            service.create_cards_batch(word_data_list)

        store_calls = [c for c in mock_post.call_args_list if c[1]["json"]["action"] == "storeMediaFile"]
        # Exactly the two unique files, despite three card-level references.
        assert len(store_calls) == 2
        names = {c[1]["json"]["params"]["filename"] for c in store_calls}
        assert names == {"d1__svg-accent_X.svg", "d1__second.svg"}


class TestAnkiTagsConfig:
    """Tests for the configurable ``anki_tags`` field.

    The note payload's ``tags`` array is derived from
    ``config.anki_tags.split()``. ``str.split()`` with no args collapses runs
    of whitespace and discards empty strings, so the empty / whitespace-only
    cases must yield an empty list rather than ``[""]``.
    """

    @pytest.mark.parametrize(
        ("anki_tags", "expected"),
        [
            ("auto-mined", ["auto-mined"]),
            ("naruto shounen", ["naruto", "shounen"]),
            ("", []),
            ("   ", []),
            ("  spaced   words  ", ["spaced", "words"]),
        ],
    )
    def test_create_cards_batch_tags_payload(self, test_config, make_tokenized_word, anki_tags, expected):
        """Batch path: every note in the batch payload uses the split tags."""
        from dataclasses import replace

        config = replace(test_config, anki_tags=anki_tags)
        service = AnkiService(config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[12345])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            count = service.create_cards_batch([CardPayload(word=word, media=media, definition="definition")])

        assert count == 1
        payload = mock_post.call_args[1]["json"]
        note = payload["params"]["notes"][0]
        assert note["tags"] == expected


class TestExtractDictMediaSrcsEnvelope:
    """Regression guard for `_extract_dict_media_srcs` against the new image
    envelope shape emitted by yomitan_renderer.

    Task 2 wraps each dict-media `<img>` in `<a class="gloss-image-link">
    <span class="gloss-image-container">...</span></a>` and gives the `<img>`
    a space-joined class list (`gloss-image anki-miner-dict-media`). The
    `_DICT_MEDIA_IMG_RE` pattern uses `\\b` boundaries so it still locates the
    marker class inside that envelope. If a future renderer change reorders
    or strips the marker, this test fails loudly.
    """

    def test_envelope_img_src_is_extracted(self):
        from anki_miner.services.anki_service import _extract_dict_media_srcs

        definition = (
            '<a class="gloss-image-link" data-path="orig/path.svg">'
            '<span class="gloss-image-container">'
            '<img class="gloss-image anki-miner-dict-media" src="my-dict__path.svg">'
            "</span>"
            "</a>"
        )

        assert _extract_dict_media_srcs(definition) == ["my-dict__path.svg"]


# ---------------------------------------------------------------------------
# TestGlossaryFieldRouting (Issue #17: multi-dict glossary field)
# ---------------------------------------------------------------------------


class TestGlossaryFieldRouting:
    """Tests for routing glossary HTML through the mapped Anki field.

    Glossary HTML is a Yomitan envelope (raw HTML) and must NOT be
    html.escape()d — it must bypass OPTIONAL_FIELD_KEYS and flow through
    field_data verbatim.
    """

    _GLOSSARY_HTML = '<div class="yomitan-glossary">' '<ol><li data-dictionary="X">X def</li></ol>' "</div>"

    def test_glossary_routed_to_mapped_anki_field(self, test_config, make_tokenized_word):
        """When anki_fields['glossary'] is set, AnkiService writes the raw HTML to that field."""
        from dataclasses import replace

        cfg = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "glossary": "Glossary"},
        )
        service = AnkiService(cfg)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[123])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            result = service.create_cards_batch(
                [
                    CardPayload(
                        word=word,
                        media=media,
                        definition="single-def",
                        extra_fields={"glossary": self._GLOSSARY_HTML},
                    )
                ]
            )

        assert result == 1
        payload = mock_post.call_args[1]["json"]
        note = payload["params"]["notes"][0]
        assert note["fields"]["Glossary"] == self._GLOSSARY_HTML
        # MainDefinition still gets the positional definition string
        assert note["fields"]["definition"] == "single-def"

    def test_glossary_skipped_when_field_mapping_empty(self, test_config, make_tokenized_word):
        """Default config (anki_fields['glossary'] == '') means Glossary is never sent."""
        # test_config has no 'glossary' key → .get("glossary", "") returns "" → skipped
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[123])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            result = service.create_cards_batch(
                [
                    CardPayload(
                        word=word,
                        media=media,
                        definition="single-def",
                        extra_fields={"glossary": self._GLOSSARY_HTML},
                    )
                ]
            )

        assert result == 1
        payload = mock_post.call_args[1]["json"]
        note = payload["params"]["notes"][0]
        assert "Glossary" not in note["fields"]
        assert "" not in note["fields"]

    def test_glossary_not_html_escaped(self, test_config, make_tokenized_word):
        """Glossary HTML tags must reach AnkiConnect unescaped."""
        from dataclasses import replace

        cfg = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "glossary": "Glossary"},
        )
        service = AnkiService(cfg)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[123])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.create_cards_batch(
                [
                    CardPayload(
                        word=word,
                        media=media,
                        definition="def",
                        extra_fields={"glossary": self._GLOSSARY_HTML},
                    )
                ]
            )

        payload = mock_post.call_args[1]["json"]
        note = payload["params"]["notes"][0]
        # Must contain literal angle brackets, not &lt; / &gt;
        assert "<" in note["fields"]["Glossary"]
        assert "&lt;" not in note["fields"]["Glossary"]

    def test_other_extra_fields_survive_glossary_extraction(self, test_config, make_tokenized_word):
        """Pulling glossary out of extra_fields must not discard other keys."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[123])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            result = service.create_cards_batch(
                [
                    CardPayload(
                        word=word,
                        media=media,
                        definition="def",
                        extra_fields={
                            "glossary": self._GLOSSARY_HTML,
                            "pitch_position": "1",
                            "pitch_category": "頭高",
                            "frequency": "200",
                        },
                    )
                ]
            )

        assert result == 1
        payload = mock_post.call_args[1]["json"]
        note = payload["params"]["notes"][0]
        # Optional fields still routed normally
        assert note["fields"]["PitchPosition"] == "1"
        assert note["fields"]["PitchCategory"] == "頭高"
        assert note["fields"]["Frequency"] == "200"

    def test_glossary_dict_media_uploaded(self, test_config, temp_dir, make_tokenized_word):
        """dict-media images embedded in glossary HTML must be uploaded to Anki."""
        from dataclasses import replace

        dicts_root = temp_dir / "dicts"
        media_dir = dicts_root / "test-dict" / "media"
        media_dir.mkdir(parents=True)
        (media_dir / "svg-pitch_X.svg").write_bytes(b"<svg/>")

        cfg = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "glossary": "Glossary"},
            dicts_root=dicts_root,
        )
        service = AnkiService(cfg)
        word = make_tokenized_word()
        media = MediaData()

        glossary_with_media = (
            '<div class="yomitan-glossary">'
            '<img class="anki-miner-dict-media" src="test-dict__svg-pitch_X.svg">'
            "</div>"
        )

        store_resp = _mock_response(result=None)
        create_resp = _mock_response(result=[123])

        with patch(
            "anki_miner.services._ankiconnect.requests.post", side_effect=[store_resp, create_resp]
        ) as mock_post:
            result = service.create_cards_batch(
                [
                    CardPayload(
                        word=word,
                        media=media,
                        definition="def",
                        extra_fields={"glossary": glossary_with_media},
                    )
                ]
            )

        assert result == 1
        store_calls = [c for c in mock_post.call_args_list if c[1]["json"]["action"] == "storeMediaFile"]
        assert len(store_calls) == 1
        assert store_calls[0][1]["json"]["params"]["filename"] == "test-dict__svg-pitch_X.svg"

    def test_no_glossary_in_extra_fields_does_not_crash(self, test_config, make_tokenized_word):
        """extra_fields without a glossary key must still work fine."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[123])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.create_cards_batch(
                [
                    CardPayload(
                        word=word,
                        media=media,
                        definition="def",
                        extra_fields={"pitch_position": "0"},
                    )
                ]
            )

        assert result == 1

    def test_none_extra_fields_does_not_crash(self, test_config, make_tokenized_word):
        """CardPayload with default extra_fields=None must still work fine after glossary wiring."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[123])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.create_cards_batch([CardPayload(word=word, media=media, definition="def")])

        assert result == 1


# ---------------------------------------------------------------------------
# TestExistingVocabCache
# ---------------------------------------------------------------------------


class TestExistingVocabCache:
    """Tests for the session-scoped vocabulary cache on AnkiService."""

    def _find_resp(self, ids=(1,)):
        return _mock_response(result=list(ids))

    def _notes_resp(self, words):
        return _mock_response(result=[{"fields": {"word": {"value": w}}} for w in words])

    def test_second_call_returns_cached_without_requerying(self, test_config):
        """Second call must return the cached set without hitting AnkiConnect again."""
        service = AnkiService(test_config)

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[["1"], [{"fields": {"word": {"value": "食べる"}}}]],
        ) as mock_pa:
            result1 = service.get_existing_vocabulary()
            result2 = service.get_existing_vocabulary()

        # post_action should only have been called for the first query
        assert mock_pa.call_count == 2  # findNotes + notesInfo, then no more
        assert result1 == result2 == {"食べる"}

    def test_cache_starts_as_none(self, test_config):
        """Cache field should be None before first call."""
        service = AnkiService(test_config)
        assert service._existing_vocab_cache is None

    def test_cache_populated_after_first_call(self, test_config):
        """Cache is set after a successful get_existing_vocabulary call."""
        service = AnkiService(test_config)

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[1], [{"fields": {"word": {"value": "飲む"}}}]],
        ):
            service.get_existing_vocabulary()

        assert service._existing_vocab_cache == {"飲む"}

    def test_invalidate_sets_cache_to_none(self, test_config):
        """invalidate_existing_vocabulary_cache() resets cache to None."""
        service = AnkiService(test_config)

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[1], [{"fields": {"word": {"value": "走る"}}}]],
        ):
            service.get_existing_vocabulary()

        assert service._existing_vocab_cache is not None
        service.invalidate_existing_vocabulary_cache()
        assert service._existing_vocab_cache is None

    def test_invalidate_forces_refresh_on_next_call(self, test_config):
        """After invalidation, next call re-queries AnkiConnect."""
        service = AnkiService(test_config)

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[1], [{"fields": {"word": {"value": "走る"}}}]],
        ):
            service.get_existing_vocabulary()

        service.invalidate_existing_vocabulary_cache()

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[2], [{"fields": {"word": {"value": "走る"}}, "fields2": {}}]],
        ) as mock_pa2:
            service.get_existing_vocabulary()

        # Should re-query after invalidation
        assert mock_pa2.call_count > 0

    def test_create_cards_batch_invalidates_on_success(self, test_config, make_tokenized_word):
        """create_cards_batch must invalidate the cache when cards are created."""
        service = AnkiService(test_config)
        # Warm the cache
        service._existing_vocab_cache = {"食べる"}
        assert service._existing_vocab_cache is not None

        word = make_tokenized_word()
        media = MediaData()
        resp = _mock_response(result=[12345])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            service.create_cards_batch([CardPayload(word=word, media=media, definition="def")])

        assert service._existing_vocab_cache is None

    def test_create_cards_batch_no_invalidation_when_zero_created(self, test_config, make_tokenized_word):
        """Cache must NOT be invalidated when all note IDs come back null (zero created)."""
        service = AnkiService(test_config)
        service._existing_vocab_cache = {"食べる"}

        word = make_tokenized_word()
        media = MediaData()
        # All IDs null → total_created == 0
        resp = _mock_response(result=[None, None])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            service.create_cards_batch([CardPayload(word=word, media=media, definition="def")])

        # Cache preserved — nothing was actually added
        assert service._existing_vocab_cache == {"食べる"}

    def test_create_cards_batch_no_invalidation_on_empty_list(self, test_config):
        """Cache must NOT be invalidated for an empty submission."""
        service = AnkiService(test_config)
        service._existing_vocab_cache = {"食べる"}

        service.create_cards_batch([])

        assert service._existing_vocab_cache == {"食べる"}

    def test_second_call_hits_no_post_action(self, test_config):
        """After caching, a second call must not invoke post_action at all."""
        service = AnkiService(test_config)

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[1], [{"fields": {"word": {"value": "食べる"}}}]],
        ) as mock_pa:
            service.get_existing_vocabulary()
            call_count_after_first = mock_pa.call_count

            # Second call
            service.get_existing_vocabulary()
            call_count_after_second = mock_pa.call_count

        # No additional calls after the first population
        assert call_count_after_second == call_count_after_first


# ---------------------------------------------------------------------------
# TestExcludedDecks (Issue #38)
# ---------------------------------------------------------------------------


class TestBuildVocabQuery:
    """Tests for AnkiService._build_vocab_query."""

    def test_no_exclusions_scans_whole_collection(self, test_config):
        """With no excluded decks the query is the bare deck:* wildcard."""
        service = AnkiService(test_config)
        assert service._build_vocab_query() == "deck:*"

    def test_single_exclusion_negated_and_quoted(self, test_config):
        """An excluded deck is appended as a quoted, negated clause."""
        from dataclasses import replace

        service = AnkiService(replace(test_config, excluded_decks=("Remembering The Kanji",)))
        assert service._build_vocab_query() == 'deck:* -deck:"Remembering The Kanji"'

    def test_multiple_exclusions_in_order(self, test_config):
        """Each excluded deck gets its own negated clause."""
        from dataclasses import replace

        service = AnkiService(replace(test_config, excluded_decks=("RTK", "Kanji Writing")))
        assert service._build_vocab_query() == 'deck:* -deck:"RTK" -deck:"Kanji Writing"'

    def test_quotes_and_backslashes_escaped(self, test_config):
        """Deck names with quotes/backslashes must not break the query string."""
        from dataclasses import replace

        service = AnkiService(replace(test_config, excluded_decks=('My "Quoted" Deck', "back\\slash")))
        assert service._build_vocab_query() == 'deck:* -deck:"My \\"Quoted\\" Deck" -deck:"back\\\\slash"'

    def test_glob_metachars_escaped(self, test_config):
        """Deck names with Anki wildcards (_ , *) must be escaped to match literally."""
        from dataclasses import replace

        service = AnkiService(replace(test_config, excluded_decks=("Core_2k", "Wild*Card")))
        assert service._build_vocab_query() == 'deck:* -deck:"Core\\_2k" -deck:"Wild\\*Card"'


class TestGetExistingVocabularyExcludesDecks:
    """get_existing_vocabulary must pass the exclusion-aware query to findNotes."""

    def test_findnotes_receives_built_query(self, test_config):
        """The findNotes call uses the excluded-deck query, not a bare deck:*."""
        from dataclasses import replace

        service = AnkiService(replace(test_config, excluded_decks=("RTK",)))

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[1], [{"fields": {"word": {"value": "食べる"}}}]],
        ) as mock_pa:
            result = service.get_existing_vocabulary()

        assert result == {"食べる"}
        first_call = mock_pa.call_args_list[0]
        assert first_call.args[1] == "findNotes"
        assert first_call.kwargs["params"] == {"query": 'deck:* -deck:"RTK"'}


class TestGetDeckNames:
    """Tests for AnkiService.get_deck_names."""

    def test_returns_deck_list(self, test_config):
        """Should return the deckNames result as a list."""
        service = AnkiService(test_config)
        resp = _mock_response(result=["Default", "RTK", "Mining"])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            assert service.get_deck_names() == ["Default", "RTK", "Mining"]

    def test_connection_error_returns_empty(self, test_config):
        """Should swallow AnkiConnectionError and return an empty list."""
        service = AnkiService(test_config)
        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=AnkiConnectionError("down"),
        ):
            assert service.get_deck_names() == []


class TestModelStyling:
    """Tests for AnkiService.get_model_styling / update_model_styling (Issue #44)."""

    def test_get_returns_css(self, test_config):
        service = AnkiService(test_config)
        resp = _mock_response(result={"css": ".card{color:red}"})
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            assert service.get_model_styling() == ".card{color:red}"
        payload = mock_post.call_args[1]["json"]
        assert payload["action"] == "modelStyling"
        assert payload["params"]["modelName"] == test_config.anki_note_type

    def test_get_uses_explicit_model_name(self, test_config):
        service = AnkiService(test_config)
        resp = _mock_response(result={"css": ""})
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.get_model_styling("Lapis")
        assert mock_post.call_args[1]["json"]["params"]["modelName"] == "Lapis"

    def test_get_returns_empty_when_no_css_key(self, test_config):
        """A response without a usable ``css`` value degrades to an empty string."""
        service = AnkiService(test_config)
        resp = _mock_response(result={})
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            assert service.get_model_styling() == ""

    def test_get_propagates_connection_error(self, test_config):
        """Unlike the swallowing fetch helpers, errors must propagate."""
        service = AnkiService(test_config)
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=AnkiConnectionError("Is Anki running?"),
            ),
            pytest.raises(AnkiConnectionError),
        ):
            service.get_model_styling()

    def test_get_propagates_missing_model_error(self, test_config):
        """An AnkiConnect error payload (model not found) surfaces, not swallowed."""
        service = AnkiService(test_config)
        resp = _mock_response(error="model was not found: Lapis")
        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            pytest.raises(AnkiConnectionError),
        ):
            service.get_model_styling("Lapis")

    def test_update_sends_model_shape(self, test_config):
        service = AnkiService(test_config)
        resp = _mock_response(result=None)
        css = ".yomitan-glossary{color:red}"
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service.update_model_styling(css, "Lapis")
        payload = mock_post.call_args[1]["json"]
        assert payload["action"] == "updateModelStyling"
        assert payload["params"]["model"] == {"name": "Lapis", "css": css}

    def test_update_propagates_connection_error(self, test_config):
        service = AnkiService(test_config)
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=AnkiConnectionError("down"),
            ),
            pytest.raises(AnkiConnectionError),
        ):
            service.update_model_styling(".x{}")


# ---------------------------------------------------------------------------
# TestEnsureDeck
# ---------------------------------------------------------------------------


class TestEnsureDeck:
    """Tests for AnkiService.ensure_deck."""

    def test_issues_create_deck_action_with_correct_params(self, test_config):
        """Should call createDeck with the deck name and the configured URL."""
        service = AnkiService(test_config)
        with patch(
            "anki_miner.services.anki_service.post_action",
            return_value=1234,
        ) as mock_pa:
            service.ensure_deck("Some Deck")

        mock_pa.assert_called_once_with(
            test_config.ankiconnect_url,
            "createDeck",
            params={"deck": "Some Deck"},
            timeout=15,
        )

    def test_existing_deck_returns_id_and_does_not_raise(self, test_config):
        """When AnkiConnect returns an existing deck id, ensure_deck must not raise."""
        service = AnkiService(test_config)
        with patch(
            "anki_miner.services.anki_service.post_action",
            return_value=9999,  # existing deck id
        ):
            service.ensure_deck("Existing Deck")  # must not raise

    def test_anki_connection_error_propagates(self, test_config):
        """Should raise AnkiConnectionError when post_action fails (e.g. Anki down)."""
        service = AnkiService(test_config)
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=AnkiConnectionError("Cannot connect to AnkiConnect"),
            ),
            pytest.raises(AnkiConnectionError, match="Cannot connect"),
        ):
            service.ensure_deck("New Deck")

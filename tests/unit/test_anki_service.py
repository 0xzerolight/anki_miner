"""Tests for anki_service module."""

import base64
import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.exceptions import AnkiConnectionError, SetupError
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


class TestGetExistingVocabularySecondBatchTimeout:
    """A Timeout on a LATER notesInfo batch (not the first) must degrade the
    SAME way as a first-batch failure: empty set + warning, and the cache must
    stay unpopulated so the next run re-queries.

    The whole method runs under one try/except, so a mid-pagination Timeout
    discards the words already collected from earlier batches — the degraded
    return is a fresh empty set, NOT a partial result. Pinning this guards
    against a refactor that accidentally caches the partial set (which would
    make later-batch words look unknown forever) or returns it as if complete.
    """

    def _two_batch_find_resp(self):
        """1500 note IDs -> two notesInfo batches (1000 + 500)."""
        return _mock_response(result=list(range(1, 1501)))

    def test_returns_empty_set_not_partial(self, test_config):
        service = AnkiService(test_config)

        find_resp = self._two_batch_find_resp()
        batch1_resp = _mock_response(result=[{"fields": {"word": {"value": f"語{i}"}}} for i in range(1000)])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[find_resp, batch1_resp, requests.exceptions.Timeout("batch 2 timed out")],
        ):
            result = service.get_existing_vocabulary()

        # Degraded to empty — the 1000 words from batch 1 are NOT returned.
        assert result == set()

    def test_logs_warning(self, test_config, caplog):
        service = AnkiService(test_config)

        find_resp = self._two_batch_find_resp()
        batch1_resp = _mock_response(result=[{"fields": {"word": {"value": f"語{i}"}}} for i in range(1000)])

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=[find_resp, batch1_resp, requests.exceptions.Timeout("batch 2 timed out")],
            ),
            caplog.at_level(logging.WARNING),
        ):
            service.get_existing_vocabulary()

        assert "Failed to fetch existing vocabulary" in caplog.text

    def test_cache_stays_none_so_next_call_requeries(self, test_config):
        """The degraded path returns before assigning the cache; a subsequent
        call must hit AnkiConnect again rather than serve a cached empty set."""
        service = AnkiService(test_config)

        find_resp = self._two_batch_find_resp()
        batch1_resp = _mock_response(result=[{"fields": {"word": {"value": f"語{i}"}}} for i in range(1000)])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[find_resp, batch1_resp, requests.exceptions.Timeout("batch 2 timed out")],
        ):
            service.get_existing_vocabulary()

        # Internal cache field is the source of truth for the "re-query" contract.
        assert service._existing_vocab_cache is None

        # A clean follow-up run succeeds (cache was not poisoned with empty set).
        good_find = _mock_response(result=[1, 2])
        good_notes = _mock_response(
            result=[
                {"fields": {"word": {"value": "食べる"}}},
                {"fields": {"word": {"value": "飲む"}}},
            ]
        )
        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[good_find, good_notes],
        ) as mock_post:
            result = service.get_existing_vocabulary()

        assert result == {"食べる", "飲む"}
        assert mock_post.called  # re-queried, not served from cache


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
            media = MediaData()  # no files to avoid media-store IO
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

    def test_mid_run_batch_failure_persists_earlier_batches(self, test_config, make_tokenized_word):
        """A non-duplicate failure in a later batch must not discard earlier
        batches' results: their note IDs stay recorded (so Undo works) and the
        vocab cache is invalidated (it is now stale), while the error still
        propagates to the pipeline boundary."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=150)  # 2 batches (100 + 50)

        # Prime the vocab cache so we can assert it gets invalidated.
        service._existing_vocab_cache = {"既知"}

        # Batch 1 succeeds with ids 0..99; batch 2 raises a non-duplicate error.
        batch1_resp = _mock_response(result=list(range(100)))
        batch2_err = _mock_response(error="deck was not found: Anki Miner")

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=[batch1_resp, batch2_err],
            ),
            pytest.raises(AnkiConnectionError, match="deck was not found"),
        ):
            service.create_cards_batch(items)

        # Batch-1 cards exist in Anki — their IDs must be recorded for Undo.
        assert service.last_created_note_ids == list(range(100))
        # The cache reflected the pre-run collection and is now stale.
        assert service._existing_vocab_cache is None

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
        """Files with a path set but missing on disk: no upload attempt, counted as failures."""
        service = AnkiService(test_config)

        word = make_tokenized_word()
        # Paths set but files not created on disk (vanished between pipeline and upload)
        media = MediaData(
            screenshot_path=tmp_path / "missing.jpg",
            audio_path=tmp_path / "missing.mp3",
            screenshot_filename="missing.jpg",
            audio_filename="missing.mp3",
        )

        resp = _mock_response(result="ok")

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

        # No HTTP calls because files don't exist on disk.
        mock_post.assert_not_called()
        # Both vanished files count toward the failure total (not silent).
        assert service.last_media_store_failures == 2

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
            caplog.at_level(logging.WARNING, logger="anki_miner.services.anki_media_store"),
        ):
            stored = service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

        assert any("silently skipped" in r.message for r in caplog.records)
        # Only the one result that came back should be counted
        assert "shot.jpg" in stored
        assert "clip.mp3" not in stored

    def test_multi_failure_falls_back_to_per_file(self, test_config, make_tokenized_word, tmp_path):
        """A transport failure on the multi POST should retry the chunk per-file."""
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

        # multi POST resets the connection; each per-file storeMediaFile succeeds.
        side_effect = [
            requests.exceptions.ConnectionError("connection reset"),
            _mock_response(result="shot.jpg"),
            _mock_response(result="clip.mp3"),
        ]

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=side_effect) as mock_post:
            stored = service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

        # 1 failed multi + 2 per-file storeMediaFile retries
        assert mock_post.call_count == 3
        actions = [c[1]["json"]["action"] for c in mock_post.call_args_list]
        assert actions == ["multi", "storeMediaFile", "storeMediaFile"]
        assert stored == {"shot.jpg", "clip.mp3"}
        assert service.last_media_store_failures == 0

    def test_size_aware_chunking_splits_large_payload(self, test_config, make_tokenized_word, tmp_path):
        """Files whose cumulative base64 size exceeds the byte budget split into multiple POSTs."""
        service = AnkiService(test_config)

        items = []
        for i in range(3):
            word = make_tokenized_word(lemma=f"word_{i}")
            ss_path = tmp_path / f"shot_{i}.jpg"
            ss_path.write_bytes(b"x" * 300)  # ~400 base64 chars, over the patched budget
            media = MediaData(screenshot_path=ss_path, screenshot_filename=f"shot_{i}.jpg")
            items.append(CardPayload(word=word, media=media, definition=f"def_{i}"))

        resp = _mock_response(result=[None])  # one non-error sub-result per single-file chunk

        with (
            patch("anki_miner.services.anki_media_store._MEDIA_BATCH_MAX_BYTES", 100),
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post,
        ):
            stored = service._store_media_files_batch(items)

        # Each oversized file flushes its own multi chunk → 3 POSTs, each with 1 action
        assert mock_post.call_count == 3
        for call in mock_post.call_args_list:
            payload = call[1]["json"]
            assert payload["action"] == "multi"
            assert len(payload["params"]["actions"]) == 1
        assert stored == {f"shot_{i}.jpg" for i in range(3)}

    def test_duplicate_filenames_read_and_encoded_once(self, test_config, make_tokenized_word, tmp_path):
        """A filename shared by N payloads (audiobook cover art) is built once."""
        from anki_miner.services import anki_media_store

        service = AnkiService(test_config)

        cover_path = tmp_path / "cover.jpg"
        cover_path.write_bytes(b"cover-data")

        items = []
        for i in range(3):
            au_path = tmp_path / f"clip_{i}.mp3"
            au_path.write_bytes(b"audio-data")
            media = MediaData(
                screenshot_path=cover_path,
                audio_path=au_path,
                screenshot_filename="cover.jpg",
                audio_filename=f"clip_{i}.mp3",
            )
            items.append(CardPayload(word=make_tokenized_word(lemma=f"word_{i}"), media=media, definition=f"def_{i}"))

        # 4 deduplicated actions (cover + 3 clips), all successful
        resp = _mock_response(result=[None, None, None, None])

        with (
            patch.object(
                anki_media_store,
                "_build_store_media_action",
                wraps=anki_media_store._build_store_media_action,
            ) as build_mock,
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post,
        ):
            stored = service._store_media_files_batch(items)

        # The shared cover is read + base64-encoded once, not once per payload
        built_filenames = [c.args[0] for c in build_mock.call_args_list]
        assert built_filenames.count("cover.jpg") == 1
        assert build_mock.call_count == 4

        # And shipped once in the multi POST
        payload = mock_post.call_args[1]["json"]
        filenames_sent = [a["params"]["filename"] for a in payload["params"]["actions"]]
        assert filenames_sent.count("cover.jpg") == 1
        assert stored == {"cover.jpg", "clip_0.mp3", "clip_1.mp3", "clip_2.mp3"}

    def test_total_failure_sets_failure_counter(self, test_config, make_tokenized_word, tmp_path):
        """When multi and the per-file fallback both fail, the failure count is recorded."""
        service = AnkiService(test_config)

        word = make_tokenized_word()
        ss_path = tmp_path / "shot.jpg"
        ss_path.write_bytes(b"screenshot-data")
        media = MediaData(screenshot_path=ss_path, screenshot_filename="shot.jpg")

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=requests.exceptions.ConnectionError("reset"),
        ):
            stored = service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

        assert stored == set()
        assert service.last_media_store_failures == 1

    def test_vanished_source_file_counts_as_failure(self, test_config, make_tokenized_word, tmp_path):
        """A file whose path was set but vanished before upload counts as a failure."""
        service = AnkiService(test_config)

        word = make_tokenized_word()
        # Create a path that points to a non-existent file (never written to disk).
        media = MediaData(
            screenshot_path=tmp_path / "vanished.jpg",
            screenshot_filename="vanished.jpg",
        )

        with patch("anki_miner.services._ankiconnect.requests.post") as mock_post:
            stored = service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

        # Nothing to upload — no HTTP call made.
        mock_post.assert_not_called()
        assert stored == set()
        # But the vanished file counts toward the failure total.
        assert service.last_media_store_failures == 1

    def test_none_filename_or_path_not_counted_as_failure(self, test_config, make_tokenized_word, tmp_path):
        """Legitimately absent media (filename or path is None) stays silent — not a failure."""
        service = AnkiService(test_config)

        au_path = tmp_path / "clip.mp3"
        au_path.write_bytes(b"audio-data")
        # No screenshot at all (both filename and path absent) — legitimately silent.
        media = MediaData(audio_path=au_path, audio_filename="clip.mp3")

        resp = _mock_response(result=["clip.mp3"])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            service._store_media_files_batch([CardPayload(word=make_tokenized_word(), media=media, definition="def")])

        assert service.last_media_store_failures == 0

    def test_vanished_expression_audio_counts_as_failure(self, test_config, make_tokenized_word, tmp_path):
        """Vanished expression_audio file (Issue #73 field) is counted in the failure total."""
        service = AnkiService(test_config)

        au_path = tmp_path / "clip.mp3"
        au_path.write_bytes(b"audio-data")
        # Expression audio path set but file was deleted (e.g. audio_cache cleared mid-run).
        media = MediaData(
            audio_path=au_path,
            audio_filename="clip.mp3",
            expression_audio_path=tmp_path / "食べる_exp.mp3",
            expression_audio_filename="食べる_exp.mp3",
        )

        resp = _mock_response(result=["clip.mp3"])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            stored = service._store_media_files_batch(
                [CardPayload(word=make_tokenized_word(), media=media, definition="def")]
            )

        # clip.mp3 uploads fine; expression audio vanished → 1 failure.
        assert "clip.mp3" in stored
        assert service.last_media_store_failures == 1


# ---------------------------------------------------------------------------
# TestExpressionAudio
# ---------------------------------------------------------------------------


class TestExpressionAudio:
    """Expression audio routing through media upload and note build (Issue #73)."""

    def test_media_data_defaults_none(self):
        """New MediaData fields default to None (no expression audio)."""
        media = MediaData()
        assert media.expression_audio_path is None
        assert media.expression_audio_filename is None

    def test_expression_audio_is_optional_not_required(self):
        """expression_audio must be optional: configs without it keep working."""
        assert "expression_audio" in AnkiService.OPTIONAL_FIELD_KEYS
        assert "expression_audio" not in AnkiService.REQUIRED_FIELD_KEYS

    def _config_with_expression_audio(self, test_config, field_name="ExpressionAudio"):
        from dataclasses import replace

        return replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": field_name},
        )

    def test_build_note_emits_sound_ref_when_stored_and_mapped(self, test_config, make_tokenized_word):
        """Filename present + stored + mapping non-empty → [sound:...] in mapped field."""
        from anki_miner.services.anki_note_builder import build_note

        config = self._config_with_expression_audio(test_config)
        media = MediaData(expression_audio_filename="食べる_exp.mp3")
        item = CardPayload(word=make_tokenized_word(), media=media, definition="def")

        built = build_note(item, config, stored_files={"食べる_exp.mp3"})

        assert built.note["fields"]["ExpressionAudio"] == "[sound:食べる_exp.mp3]"

    def test_build_note_field_absent_when_mapping_blank(self, test_config, make_tokenized_word):
        """Blank mapping → no expression-audio field on the note at all."""
        from dataclasses import replace

        from anki_miner.services.anki_note_builder import build_note

        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": ""},
        )
        media = MediaData(expression_audio_filename="食べる_exp.mp3")
        item = CardPayload(word=make_tokenized_word(), media=media, definition="def")

        built = build_note(item, config, stored_files={"食べる_exp.mp3"})

        assert "[sound:食べる_exp.mp3]" not in built.note["fields"].values()
        assert "ExpressionAudio" not in built.note["fields"]

    def test_build_note_field_empty_when_filename_none(self, test_config, make_tokenized_word):
        """Mapping set but no expression audio fetched → field stays empty."""
        from anki_miner.services.anki_note_builder import build_note

        config = self._config_with_expression_audio(test_config)
        item = CardPayload(word=make_tokenized_word(), media=MediaData(), definition="def")

        built = build_note(item, config, stored_files=set())

        assert built.note["fields"]["ExpressionAudio"] == ""

    def test_build_note_field_empty_when_not_stored(self, test_config, make_tokenized_word):
        """Filename set but upload failed (not in stored_files) → field stays empty."""
        from anki_miner.services.anki_note_builder import build_note

        config = self._config_with_expression_audio(test_config)
        media = MediaData(expression_audio_filename="食べる_exp.mp3")
        item = CardPayload(word=make_tokenized_word(), media=media, definition="def")

        built = build_note(item, config, stored_files=set())

        assert built.note["fields"]["ExpressionAudio"] == ""

    def test_build_note_config_without_key_does_not_crash(self, test_config, make_tokenized_word):
        """Old configs without the expression_audio key keep working (optional)."""
        from anki_miner.services.anki_note_builder import build_note

        assert "expression_audio" not in test_config.anki_fields
        media = MediaData(expression_audio_filename="食べる_exp.mp3")
        item = CardPayload(word=make_tokenized_word(), media=media, definition="def")

        built = build_note(item, test_config, stored_files={"食べる_exp.mp3"})

        assert "[sound:食べる_exp.mp3]" not in built.note["fields"].values()

    def test_store_batch_includes_expression_audio(self, test_config, make_tokenized_word, tmp_path):
        """Expression audio file ships in the same upload batch as card media."""
        service = AnkiService(test_config)

        exp_path = tmp_path / "食べる_exp.mp3"
        exp_path.write_bytes(b"expression-audio-data")
        au_path = tmp_path / "clip.mp3"
        au_path.write_bytes(b"audio-data")
        media = MediaData(
            audio_path=au_path,
            audio_filename="clip.mp3",
            expression_audio_path=exp_path,
            expression_audio_filename="食べる_exp.mp3",
        )

        resp = _mock_response(result=["clip.mp3", "食べる_exp.mp3"])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            stored = service._store_media_files_batch(
                [CardPayload(word=make_tokenized_word(), media=media, definition="def")]
            )

        payload = mock_post.call_args[1]["json"]
        filenames_sent = [a["params"]["filename"] for a in payload["params"]["actions"]]
        assert "食べる_exp.mp3" in filenames_sent
        assert stored == {"clip.mp3", "食べる_exp.mp3"}

    def test_store_batch_skips_expression_audio_when_none(self, test_config, make_tokenized_word, tmp_path):
        """No expression audio on the payload → nothing extra in the batch."""
        service = AnkiService(test_config)

        au_path = tmp_path / "clip.mp3"
        au_path.write_bytes(b"audio-data")
        media = MediaData(audio_path=au_path, audio_filename="clip.mp3")

        resp = _mock_response(result=["clip.mp3"])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            stored = service._store_media_files_batch(
                [CardPayload(word=make_tokenized_word(), media=media, definition="def")]
            )

        payload = mock_post.call_args[1]["json"]
        assert len(payload["params"]["actions"]) == 1
        assert stored == {"clip.mp3"}

    def test_store_batch_dedupes_shared_expression_audio_filename(self, test_config, make_tokenized_word, tmp_path):
        """Two words sharing one deterministic expression-audio filename → one upload action."""
        service = AnkiService(test_config)

        exp_path = tmp_path / "食べる_exp.mp3"
        exp_path.write_bytes(b"expression-audio-data")
        items = [
            CardPayload(
                word=make_tokenized_word(lemma=f"word_{i}"),
                media=MediaData(
                    expression_audio_path=exp_path,
                    expression_audio_filename="食べる_exp.mp3",
                ),
                definition=f"def_{i}",
            )
            for i in range(2)
        ]

        resp = _mock_response(result=["食べる_exp.mp3"])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            stored = service._store_media_files_batch(items)

        payload = mock_post.call_args[1]["json"]
        filenames_sent = [a["params"]["filename"] for a in payload["params"]["actions"]]
        assert filenames_sent == ["食べる_exp.mp3"]
        assert stored == {"食べる_exp.mp3"}
        assert service.last_media_store_failures == 0


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


class TestSourceField:
    """Tests for the optional "source" field (Issue #69)."""

    def test_source_in_optional_field_keys(self):
        """`source` must be a recognized optional field key."""
        assert "source" in AnkiService.OPTIONAL_FIELD_KEYS

    def _config_with_source(self, temp_dir, source_field_name):
        from anki_miner.config import AnkiMinerConfig

        return AnkiMinerConfig(
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
                "pitch_position": "",
                "pitch_category": "",
                "frequency": "",
                "source": source_field_name,
            },
            media_temp_folder=temp_dir / "temp",
            jmdict_path=temp_dir / "dict",
        )

    def test_source_written_when_mapped(self, temp_dir, make_tokenized_word):
        """A mapped `source` field renders the extra_fields value into the note."""
        config = self._config_with_source(temp_dir, "Source")
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
                        extra_fields={"source": "Foo @ 00:01:02"},
                    )
                ]
            )

        note_fields = mock_post.call_args[1]["json"]["params"]["notes"][0]["fields"]
        assert note_fields["Source"] == "Foo @ 00:01:02"

    def test_source_not_written_when_unmapped(self, temp_dir, make_tokenized_word):
        """With the default empty mapping, `source` is never written."""
        config = self._config_with_source(temp_dir, "")
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
                        extra_fields={"source": "Foo @ 00:01:02"},
                    )
                ]
            )

        note_fields = mock_post.call_args[1]["json"]["params"]["notes"][0]["fields"]
        assert "Foo @ 00:01:02" not in note_fields.values()
        assert "" not in note_fields

    def test_source_value_html_escaped(self, temp_dir, make_tokenized_word):
        """The source value is HTML-escaped like the other optional fields."""
        config = self._config_with_source(temp_dir, "Source")
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
                        extra_fields={"source": "A & B <ep>"},
                    )
                ]
            )

        note_fields = mock_post.call_args[1]["json"]["params"]["notes"][0]["fields"]
        assert note_fields["Source"] == "A &amp; B &lt;ep&gt;"


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

        # multi sub-result for one file, then addNotes result
        multi_resp = _mock_response(result=["test-dict__svg-accent_X.svg"])
        create_resp = _mock_response(result=[12345])

        with patch(
            "anki_miner.services._ankiconnect.requests.post", side_effect=[multi_resp, create_resp]
        ) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])

        # First call is a multi POST containing one storeMediaFile action
        multi_call = mock_post.call_args_list[0]
        multi_payload = multi_call[1]["json"]
        assert multi_payload["action"] == "multi"
        actions = multi_payload["params"]["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "storeMediaFile"
        assert actions[0]["params"]["filename"] == "test-dict__svg-accent_X.svg"
        assert base64.b64decode(actions[0]["params"]["data"]) == b"<svg/>"

    def test_uploaded_files_cached_across_calls(self, test_config, temp_dir, make_tokenized_word):
        """Same SVG referenced by many cards should upload once, not many times."""
        config = self._make_config_with_dict_media(test_config, temp_dir / "dicts")
        service = AnkiService(config)

        definition = '<img class="anki-miner-dict-media" src="test-dict__svg-accent_X.svg">'
        word = make_tokenized_word()
        media = MediaData()

        # First call: multi (one storeMediaFile action) + addNotes;
        # second and third calls: only addNotes (cache hit → no multi).
        multi_resp = _mock_response(result=["test-dict__svg-accent_X.svg"])
        create_resp = _mock_response(result=[12345])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[multi_resp, create_resp, create_resp, create_resp],
        ) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])

        # Three card creations + exactly one multi upload (for one file).
        multi_calls = [c for c in mock_post.call_args_list if c[1]["json"]["action"] == "multi"]
        assert len(multi_calls) == 1

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

        # One multi POST with two actions (both unique files in one chunk),
        # then the addNotes POST.
        multi_resp = _mock_response(result=["d1__svg-accent_X.svg", "d1__second.svg"])
        create_resp = _mock_response(result=[1, 2, 3])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[multi_resp, create_resp],
        ) as mock_post:
            service.create_cards_batch(word_data_list)

        # Exactly one multi POST containing exactly two storeMediaFile actions.
        multi_calls = [c for c in mock_post.call_args_list if c[1]["json"]["action"] == "multi"]
        assert len(multi_calls) == 1
        actions = multi_calls[0][1]["json"]["params"]["actions"]
        assert len(actions) == 2
        names = {a["params"]["filename"] for a in actions}
        assert names == {"d1__svg-accent_X.svg", "d1__second.svg"}

    def test_dict_media_uploaded_via_multi_once(self, test_config, temp_dir, make_tokenized_word):
        """Two cards referencing the SAME dict-media src → exactly one upload action in one multi POST."""
        config = self._make_config_with_dict_media(test_config, temp_dir / "dicts")
        service = AnkiService(config)

        src = "test-dict__svg-accent_X.svg"
        definition = f'<img class="anki-miner-dict-media" src="{src}">'
        word = make_tokenized_word()
        media = MediaData()

        # multi response: one sub-result for the single deduplicated action
        multi_resp = _mock_response(result=[src])
        create_resp = _mock_response(result=[1, 2])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[multi_resp, create_resp],
        ) as mock_post:
            service.create_cards_batch(
                [
                    CardPayload(word=word, media=media, definition=definition),
                    CardPayload(word=make_tokenized_word(lemma="word2"), media=media, definition=definition),
                ]
            )

        # Exactly one multi POST with exactly one storeMediaFile action
        multi_calls = [c for c in mock_post.call_args_list if c[1]["json"]["action"] == "multi"]
        assert len(multi_calls) == 1
        actions = multi_calls[0][1]["json"]["params"]["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "storeMediaFile"
        assert actions[0]["params"]["filename"] == src
        # Cached after upload
        assert src in service._dict_media_uploaded

    def test_missing_on_disk_is_cached_and_warned_not_retried(self, test_config, temp_dir, make_tokenized_word, caplog):
        """A src missing on disk: warning logged, cached to avoid retry, other srcs still upload."""
        from dataclasses import replace

        dicts_root = temp_dir / "dicts"
        media_dir = dicts_root / "d1" / "media"
        media_dir.mkdir(parents=True)
        (media_dir / "present.svg").write_bytes(b"<svg/>")

        config = replace(test_config, dicts_root=dicts_root)
        service = AnkiService(config)
        word = make_tokenized_word()
        media = MediaData()

        defs = [
            '<img class="anki-miner-dict-media" src="d1__missing.svg">',
            '<img class="anki-miner-dict-media" src="d1__present.svg">',
        ]
        word_data_list = [CardPayload(word=word, media=media, definition=d) for d in defs]

        # Only one upload action (for the present file); no action for missing
        multi_resp = _mock_response(result=["d1__present.svg"])
        create_resp = _mock_response(result=[1, 2])

        with (
            caplog.at_level(logging.WARNING),
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=[multi_resp, create_resp],
            ),
        ):
            service.create_cards_batch(word_data_list)

        # Warning issued for the missing file
        assert any("Dict media file missing" in r.message for r in caplog.records)
        # Missing file cached (so it is not retried)
        assert "d1__missing.svg" in service._dict_media_uploaded
        # Present file also cached
        assert "d1__present.svg" in service._dict_media_uploaded

    def test_same_src_across_two_batch_calls_not_reuploaded(self, test_config, temp_dir, make_tokenized_word):
        """Same src in two separate create_cards_batch calls → second call skips the upload."""
        config = self._make_config_with_dict_media(test_config, temp_dir / "dicts")
        service = AnkiService(config)

        src = "test-dict__svg-accent_X.svg"
        definition = f'<img class="anki-miner-dict-media" src="{src}">'
        word = make_tokenized_word()
        media = MediaData()

        # First call: multi (one action) + addNotes
        multi_resp = _mock_response(result=[src])
        create_resp = _mock_response(result=[12345])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[multi_resp, create_resp, create_resp],
        ) as mock_post:
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])
            # Second call: cache hit → no multi, only addNotes
            service.create_cards_batch([CardPayload(word=word, media=media, definition=definition)])

        multi_calls = [c for c in mock_post.call_args_list if c[1]["json"]["action"] == "multi"]
        # Only one multi POST across both batch calls
        assert len(multi_calls) == 1

    def test_multi_failure_falls_back_to_per_file(self, test_config, temp_dir, make_tokenized_word):
        """A transport failure on the dict-media multi POST should retry the chunk per-file."""
        config = self._make_config_with_dict_media(test_config, temp_dir / "dicts")
        (config.dicts_root / "test-dict" / "media" / "second.svg").write_bytes(b"<svg2/>")
        service = AnkiService(config)

        defs = [
            '<img class="anki-miner-dict-media" src="test-dict__svg-accent_X.svg">',
            '<img class="anki-miner-dict-media" src="test-dict__second.svg">',
        ]
        word_data_list = [
            CardPayload(word=make_tokenized_word(lemma=f"word_{i}"), media=MediaData(), definition=d)
            for i, d in enumerate(defs)
        ]

        # multi POST resets the connection; each per-file storeMediaFile
        # succeeds; addNotes then creates both cards.
        side_effect = [
            requests.exceptions.ConnectionError("connection reset"),
            _mock_response(result="test-dict__svg-accent_X.svg"),
            _mock_response(result="test-dict__second.svg"),
            _mock_response(result=[1, 2]),
        ]

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=side_effect) as mock_post:
            created = service.create_cards_batch(word_data_list)

        # 1 failed multi + 2 per-file storeMediaFile retries + addNotes
        actions = [c[1]["json"]["action"] for c in mock_post.call_args_list]
        assert actions == ["multi", "storeMediaFile", "storeMediaFile", "addNotes"]
        assert created == 2
        # Confirmed per-file stores are cached.
        assert "test-dict__svg-accent_X.svg" in service._dict_media_uploaded
        assert "test-dict__second.svg" in service._dict_media_uploaded

    def test_multi_and_per_file_failure_leaves_src_uncached_for_retry(
        self, test_config, temp_dir, make_tokenized_word, caplog
    ):
        """multi AND per-file fallback fail: cards still created, src uncached so the next batch retries."""
        config = self._make_config_with_dict_media(test_config, temp_dir / "dicts")
        service = AnkiService(config)

        src = "test-dict__svg-accent_X.svg"
        definition = f'<img class="anki-miner-dict-media" src="{src}">'
        word = make_tokenized_word()

        def fake_post(url, json=None, timeout=None):
            if json["action"] in ("multi", "storeMediaFile"):
                raise requests.exceptions.ConnectionError("connection reset")
            return _mock_response(result=[12345])

        with (
            caplog.at_level(logging.WARNING, logger="anki_miner.services.anki_media_store"),
            patch("anki_miner.services._ankiconnect.requests.post", side_effect=fake_post) as mock_post,
        ):
            created = service.create_cards_batch([CardPayload(word=word, media=MediaData(), definition=definition)])

        # Card creation survives the media failure.
        assert created == 1
        # The chunk was retried per-file before giving up.
        actions = [c[1]["json"]["action"] for c in mock_post.call_args_list]
        assert actions == ["multi", "storeMediaFile", "addNotes"]
        assert any("individually" in r.message for r in caplog.records)
        # Never confirmed stored → NOT cached, so the next batch retries it.
        assert src not in service._dict_media_uploaded

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=fake_post) as mock_post_2:
            service.create_cards_batch([CardPayload(word=word, media=MediaData(), definition=definition)])

        retry_actions = [c[1]["json"]["action"] for c in mock_post_2.call_args_list]
        assert retry_actions == ["multi", "storeMediaFile", "addNotes"]

    def test_size_aware_chunking_splits_large_payload(self, test_config, temp_dir, make_tokenized_word):
        """Dict-media files whose cumulative base64 size exceeds the byte budget split into multiple POSTs."""
        config = self._make_config_with_dict_media(test_config, temp_dir / "dicts")
        media_dir = config.dicts_root / "test-dict" / "media"
        srcs = []
        for i in range(3):
            (media_dir / f"big_{i}.svg").write_bytes(b"x" * 300)  # ~400 base64 chars, over the patched budget
            srcs.append(f"test-dict__big_{i}.svg")

        service = AnkiService(config)
        word_data_list = [
            CardPayload(
                word=make_tokenized_word(lemma=f"word_{i}"),
                media=MediaData(),
                definition=f'<img class="anki-miner-dict-media" src="{src}">',
            )
            for i, src in enumerate(srcs)
        ]

        resp = _mock_response(result=[None])  # one non-error sub-result per single-file chunk

        with (
            patch("anki_miner.services.anki_media_store._MEDIA_BATCH_MAX_BYTES", 100),
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post,
        ):
            service._upload_dict_media_batch(word_data_list)

        # Each oversized file flushes its own multi chunk → 3 POSTs, each with 1 action
        assert mock_post.call_count == 3
        for call in mock_post.call_args_list:
            payload = call[1]["json"]
            assert payload["action"] == "multi"
            assert len(payload["params"]["actions"]) == 1
        assert set(srcs) <= service._dict_media_uploaded


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
        from anki_miner.services.anki_media_store import _extract_dict_media_srcs

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

        # multi sub-result for one file, then addNotes result
        multi_resp = _mock_response(result=["test-dict__svg-pitch_X.svg"])
        create_resp = _mock_response(result=[123])

        with patch(
            "anki_miner.services._ankiconnect.requests.post", side_effect=[multi_resp, create_resp]
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
        multi_calls = [c for c in mock_post.call_args_list if c[1]["json"]["action"] == "multi"]
        assert len(multi_calls) == 1
        actions = multi_calls[0][1]["json"]["params"]["actions"]
        assert len(actions) == 1
        assert actions[0]["params"]["filename"] == "test-dict__svg-pitch_X.svg"

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


# ---------------------------------------------------------------------------
# TestVerifyCardTarget
# ---------------------------------------------------------------------------


class TestVerifyCardTarget:
    """Tests for AnkiService.verify_card_target."""

    _MODELS = ["test_note_type", "Basic", "Cloze", "Model4", "Model5", "Model6"]
    _FIELDS = [
        "word",
        "sentence",
        "definition",
        "picture",
        "audio",
        "expression_furigana",
        "sentence_furigana",
        "PitchPosition",
        "PitchCategory",
        "Frequency",
    ]

    def test_happy_path_creates_deck_after_checks(self, test_config):
        """Should call createDeck with config.anki_deck_name after modelNames + modelFieldNames."""

        service = AnkiService(test_config)
        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[self._MODELS, self._FIELDS, 1234],
        ) as mock_pa:
            service.verify_card_target()

        calls = mock_pa.call_args_list
        assert calls[0][0][1] == "modelNames"
        assert calls[1][0][1] == "modelFieldNames"
        assert calls[2][0][1] == "createDeck"
        assert calls[2][1]["params"] == {"deck": test_config.anki_deck_name}

    def test_note_type_missing_raises_setup_error(self, test_config):
        """Should raise SetupError naming the configured note type when it is absent."""
        service = AnkiService(test_config)
        other_models = ["Basic", "Cloze"]
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=[other_models],
            ) as mock_pa,
            pytest.raises(SetupError, match="test_note_type"),
        ):
            service.verify_card_target()

        mock_pa.assert_called_once()
        assert mock_pa.call_args[0][1] == "modelNames"

    def test_note_type_missing_never_creates_deck(self, test_config):
        """createDeck must not be called when the note type is absent."""
        service = AnkiService(test_config)
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=[["Basic"]],
            ) as mock_pa,
            pytest.raises(SetupError),
        ):
            service.verify_card_target()

        actions = [c[0][1] for c in mock_pa.call_args_list]
        assert "createDeck" not in actions

    def test_missing_field_raises_setup_error(self, test_config):
        """Should raise SetupError naming the missing field and available fields list."""
        # Remove "word" field from the actual model fields
        truncated_fields = [f for f in self._FIELDS if f != "word"]
        service = AnkiService(test_config)
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=[self._MODELS, truncated_fields],
            ) as mock_pa,
            pytest.raises(SetupError, match="word") as exc_info,
        ):
            service.verify_card_target()

        actions = [c[0][1] for c in mock_pa.call_args_list]
        assert "createDeck" not in actions
        assert "Available:" in str(exc_info.value)

    def test_empty_string_field_mappings_ignored(self, test_config):
        """Fields mapped to '' (unmapped) should not be required in the model."""
        # test_config already has expression_reading='', sentence_reading='', source=''
        # so _FIELDS (which omits those) should be sufficient
        assert (
            "" in test_config.anki_fields.values()
        ), "fixture must have at least one empty-string field mapping for this test to be meaningful"
        service = AnkiService(test_config)
        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[self._MODELS, self._FIELDS, 1234],
        ):
            service.verify_card_target()  # must not raise

    def test_anki_connection_error_propagates(self, test_config):
        """AnkiConnectionError from post_action must propagate unchanged."""
        service = AnkiService(test_config)
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=AnkiConnectionError("Anki is down"),
            ),
            pytest.raises(AnkiConnectionError, match="Anki is down"),
        ):
            service.verify_card_target()

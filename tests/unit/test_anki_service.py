"""Tests for anki_service module."""

import base64
import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.exceptions import AnkiConnectionError, SetupError
from anki_miner.models import AnkiWriteState, CardPayload, MediaData
from anki_miner.services import _ankiconnect
from anki_miner.services._ankiconnect import _expect_list, post_action, post_multi
from anki_miner.services.anki_media_store import _content_addressed_name
from anki_miner.services.anki_service import AnkiService, is_transient_anki_transport_error

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(result=None, error=None):
    """Create a mock requests.Response with the given AnkiConnect JSON body."""
    resp = MagicMock()
    resp.json.return_value = {"result": result, "error": error}
    return resp


class TestAnkiConnectLogging:
    """The shared HTTP seam preserves diagnostics while translating failures."""

    def test_connection_error_logs_debug_before_translation(self, caplog):
        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ConnectionError("refused"),
            ),
            caplog.at_level(logging.DEBUG, logger="anki_miner.services._ankiconnect"),
            pytest.raises(AnkiConnectionError) as exc_info,
        ):
            post_action("http://localhost:8765", "findNotes")

        record = next(r for r in caplog.records if r.getMessage().startswith("AnkiConnect connection failed:"))
        assert record.name == "anki_miner.services._ankiconnect"
        assert record.levelno == logging.DEBUG
        assert "action=findNotes" in record.getMessage()
        assert str(exc_info.value) == "Cannot connect to AnkiConnect. Is Anki running?"

    def test_http_failure_logs_status_before_translation(self, caplog):
        resp = _mock_response()
        http_response = requests.Response()
        http_response.status_code = 503
        resp.raise_for_status.side_effect = requests.HTTPError("503 Service Unavailable", response=http_response)

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            caplog.at_level(logging.WARNING, logger="anki_miner.services._ankiconnect"),
            pytest.raises(AnkiConnectionError) as exc_info,
        ):
            post_action("http://localhost:8765", "findNotes")

        record = next(r for r in caplog.records if r.getMessage().startswith("AnkiConnect request failed:"))
        assert record.name == "anki_miner.services._ankiconnect"
        assert record.levelno == logging.WARNING
        assert "status=503" in record.getMessage()
        assert str(exc_info.value) == "AnkiConnect call 'findNotes' failed: 503 Service Unavailable"

    def test_non_object_body_logs_type_before_translation(self, caplog):
        resp = MagicMock()
        resp.json.return_value = ["not", "an", "object"]

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            caplog.at_level(logging.WARNING, logger="anki_miner.services._ankiconnect"),
            pytest.raises(AnkiConnectionError) as exc_info,
        ):
            post_action("http://localhost:8765", "findNotes")

        record = next(r for r in caplog.records if r.getMessage().startswith("AnkiConnect response invalid:"))
        assert record.name == "anki_miner.services._ankiconnect"
        assert record.levelno == logging.WARNING
        assert "type=list" in record.getMessage()
        assert str(exc_info.value) == (
            "AnkiConnect 'findNotes' returned a non-object response (list); is another service listening on this port?"
        )

    def test_ankiconnect_error_logs_diagnostic_before_translation(self, caplog):
        resp = _mock_response(error="deck was not found")

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            caplog.at_level(logging.WARNING, logger="anki_miner.services._ankiconnect"),
            pytest.raises(AnkiConnectionError) as exc_info,
        ):
            post_action("http://localhost:8765", "addNotes")

        record = next(r for r in caplog.records if r.getMessage().startswith("AnkiConnect error:"))
        assert record.name == "anki_miner.services._ankiconnect"
        assert record.levelno == logging.WARNING
        assert "error=deck was not found" in record.getMessage()
        assert str(exc_info.value) == "AnkiConnect error in 'addNotes': deck was not found"

    def test_expect_list_shape_violation_logs_observed_type(self, caplog):
        with (
            caplog.at_level(logging.WARNING, logger="anki_miner.services._ankiconnect"),
            pytest.raises(AnkiConnectionError) as exc_info,
        ):
            _expect_list({"error": "x"}, "findNotes")

        record = next(r for r in caplog.records if r.getMessage().startswith("AnkiConnect response shape invalid:"))
        assert record.name == "anki_miner.services._ankiconnect"
        assert record.levelno == logging.WARNING
        assert "type=dict" in record.getMessage()
        assert str(exc_info.value) == "AnkiConnect 'findNotes' returned dict, expected a list"

    def test_request_debug_logs_param_count_without_values(self, caplog):
        distinctive_value = "PRIVATE-SENTENCE-DO-NOT-LOG"
        resp = _mock_response(result=[123])

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            caplog.at_level(logging.DEBUG, logger="anki_miner.services._ankiconnect"),
        ):
            assert post_action(
                "http://localhost:8765",
                "findNotes",
                params={"query": distinctive_value},
            ) == [123]

        record = next(r for r in caplog.records if r.getMessage().startswith("AnkiConnect request:"))
        assert record.name == "anki_miner.services._ankiconnect"
        assert record.levelno == logging.DEBUG
        assert "params=1" in record.getMessage()
        assert all(distinctive_value not in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "call",
    [
        lambda: post_action("http://localhost:8765", "findNotes"),
        lambda: post_multi(
            "http://localhost:8765",
            [{"action": "findNotes", "version": 6, "params": {"query": "deck:*"}}],
        ),
    ],
)
def test_http_error_is_not_ankiconnect_success(call):
    """HTTP failure wins over an AnkiConnect-shaped success body."""
    resp = _mock_response(result=[123])
    resp.raise_for_status.side_effect = requests.HTTPError("503 Service Unavailable")

    with (
        patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
        pytest.raises(AnkiConnectionError, match="503 Service Unavailable"),
    ):
        call()

    resp.json.assert_not_called()


class TestAnkiConnectResponseSizeCap:
    """A body over the cap fails closed before json() parses it."""

    def test_post_action_oversized_body_raises_before_parsing(self, monkeypatch):
        monkeypatch.setattr(_ankiconnect, "_MAX_RESPONSE_BYTES", 10)
        resp = MagicMock()
        resp.content = b"x" * 11

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            pytest.raises(AnkiConnectionError, match="exceeding the 10-byte cap"),
        ):
            post_action("http://localhost:8765", "notesInfo")

        resp.json.assert_not_called()

    def test_post_action_body_at_cap_is_not_rejected(self, monkeypatch):
        monkeypatch.setattr(_ankiconnect, "_MAX_RESPONSE_BYTES", 10)
        resp = _mock_response(result=[1])
        resp.content = b"x" * 10

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            assert post_action("http://localhost:8765", "findNotes") == [1]

    def test_post_multi_oversized_body_raises_before_parsing(self, monkeypatch):
        monkeypatch.setattr(_ankiconnect, "_MAX_RESPONSE_BYTES", 10)
        resp = MagicMock()
        resp.content = b"x" * 11

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            pytest.raises(AnkiConnectionError, match="exceeding the 10-byte cap"),
        ):
            post_multi("http://localhost:8765", [{"action": "findNotes"}])

        resp.json.assert_not_called()


class TestReadTimeoutCopy:
    """A read timeout names the busy-Anki cause instead of raw requests text.

    Anki accepts the TCP connection even while its main thread is busy (sync,
    dialog, database check), so 'connected' indicators stay green while the
    action times out — the message must say Anki is busy, not that the network
    failed (user report: canAddNotesWithErrorDetail read timeout at 60s with
    Anki open and 'connected').
    """

    def test_post_action_read_timeout_names_busy_anki(self):
        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ReadTimeout("read timed out"),
            ),
            pytest.raises(AnkiConnectionError) as exc_info,
        ):
            post_action("http://localhost:8765", "canAddNotesWithErrorDetail", timeout=60)

        message = str(exc_info.value)
        assert "'canAddNotesWithErrorDetail' timed out after 60s" in message
        assert "likely busy" in message
        # The cause chain is load-bearing: is_transient_anki_transport_error
        # classifies via __cause__, and the queue-level retry hangs off it.
        assert isinstance(exc_info.value.__cause__, requests.exceptions.ReadTimeout)
        assert is_transient_anki_transport_error(exc_info.value)

    def test_post_multi_read_timeout_names_busy_anki(self):
        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ReadTimeout("read timed out"),
            ),
            pytest.raises(AnkiConnectionError, match="'multi' timed out after 30s"),
        ):
            post_multi("http://localhost:8765", [{"action": "findNotes", "version": 6, "params": {}}])

    def test_connect_timeout_still_reports_cannot_connect(self):
        """ConnectTimeout is a ConnectionError first: nothing answered the port."""
        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ConnectTimeout("connect timed out"),
            ),
            pytest.raises(AnkiConnectionError, match="Cannot connect"),
        ):
            post_action("http://localhost:8765", "findNotes")


@pytest.fixture(autouse=True)
def _stub_duplicate_probe(request):
    """Neutralize the pre-add duplicate probe by default (all non-duplicate).

    Keeps every ``create_cards_batch`` test that isn't about duplicates on its
    existing addNotes-only POST sequence and call count — the probe would
    otherwise add a ``canAddNotesWithErrorDetail`` POST per batch. Tests marked
    ``real_probe`` opt out to exercise the real probe and supply their own
    ``canAddNotesWithErrorDetail`` / ``canAddNotes`` responses.
    """
    if "real_probe" in request.keywords:
        yield
        return
    with patch.object(AnkiService, "_probe_duplicates", side_effect=lambda notes: [False] * len(notes)):
        yield


class TestExpectList:
    """Direct tests for the _expect_list response-shape validator."""

    def test_valid_list_passes_through(self):
        data = [1, 2, 3]
        assert _expect_list(data, "findNotes", elem_type=int) is data

    def test_any_length_when_expected_negative(self):
        assert _expect_list([1, 2], "findNotes", -1, int) == [1, 2]

    def test_non_list_raises(self):
        with pytest.raises(AnkiConnectionError, match="expected a list"):
            _expect_list({"error": "x"}, "findNotes")

    def test_length_mismatch_raises(self):
        with pytest.raises(AnkiConnectionError, match="2 item.*expected 3"):
            _expect_list([1, 2], "addNotes", 3)

    def test_element_type_mismatch_reports_index(self):
        with pytest.raises(AnkiConnectionError, match="index 1"):
            _expect_list([1, "two", 3], "findNotes", elem_type=int)

    def test_tuple_elem_type_allows_none_slots(self):
        assert _expect_list([1, None, 3], "addNotes", 3, (int, type(None))) == [1, None, 3]

    def test_none_elem_type_skips_element_check(self):
        mixed = [1, "two", {}]
        assert _expect_list(mixed, "multi", 3) == mixed


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

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("‪寮", "寮"),  # LEFT-TO-RIGHT EMBEDDING — the observed duplicate
            ("‭寮‬", "寮"),  # LRO + POP DIRECTIONAL FORMATTING
            ("‎寮", "寮"),  # LEFT-TO-RIGHT MARK
            ("寮​", "寮"),  # ZERO WIDTH SPACE
            ("﻿寮", "寮"),  # BOM
            ("&#8234;寮", "寮"),  # escaped U+202A — stripped after html.unescape
            ("‪", ""),  # nothing but format chars normalizes to empty
        ],
    )
    def test_zero_width_format_chars_stripped(self, raw, expected):
        """Cf characters are invisible, so they must not split the dedup key.

        Anki's own first-field checksum cannot see them either, so both duplicate
        gates go blind at once and a second card gets created. This filter is the
        only layer that can catch it.
        """
        from anki_miner.services.anki_service import _strip_for_dedup

        assert _strip_for_dedup(raw) == expected

    def test_control_chars_still_collapse_to_a_space(self):
        """Cc is NOT stripped: newlines/tabs carry a word boundary the collapse keeps."""
        from anki_miner.services.anki_service import _strip_for_dedup

        assert _strip_for_dedup("入れ\n墨") == "入れ 墨"
        assert _strip_for_dedup("\n入れ墨<br>") == "入れ墨"

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

    def test_zero_width_prefixed_first_field_matches_the_mined_form(self, test_config):
        """Regression: a U+202A-prefixed Expression must key as the bare word.

        A collection card mined by Yomitan/asbplayer off Netflix subtitles carries
        LEFT-TO-RIGHT EMBEDDING on its Expression. Anki's own duplicate checksum
        cannot see it, so if this set keeps the raw form too, re-mining the word
        creates a second card and nothing reports a duplicate.
        """
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1])
        notes_resp = _mock_response(result=[{"fields": {"word": {"value": "‪寮"}}}])

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"寮"}

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

    def test_non_dict_json_body_raises(self, test_config):
        """A non-object JSON body (wrong service on the port, a proxy error page)
        must raise AnkiConnectionError instead of crashing on `.get`."""
        service = AnkiService(test_config)

        bad_body = MagicMock()
        bad_body.json.return_value = ["not", "an", "object"]

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=bad_body),
            pytest.raises(AnkiConnectionError, match="non-object response"),
        ):
            service.get_existing_vocabulary()

    def test_empty_notes_info_row_treated_absent(self, test_config):
        """A `{}` notesInfo row (deleted note) must be skipped, not crash — and
        valid rows in the same batch still contribute."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1, 2])
        notes_resp = _mock_response(
            result=[
                {},  # deleted note → treated absent
                {"fields": {"Expression": {"value": "食べる"}}},
            ]
        )

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"食べる"}

    def test_malformed_field_entry_treated_absent(self, test_config):
        """A note whose first field entry is not a {value, order} object must be
        skipped rather than crash on `.get`."""
        service = AnkiService(test_config)

        find_resp = _mock_response(result=[1, 2])
        notes_resp = _mock_response(
            result=[
                {"fields": {"Expression": "not-an-object"}},  # malformed → absent
                {"fields": {"Expression": {"value": "飲む"}}},
            ]
        )

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
            result = service.get_existing_vocabulary()

        assert result == {"飲む"}

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

    def test_empty_list_returns_no_ids(self, test_config):
        """Should return no confirmed IDs for an empty list."""
        service = AnkiService(test_config)

        result = service.create_cards_batch([])

        assert result == []

    def test_single_batch_under_fifty(self, test_config, make_tokenized_word, recording_progress):
        """Should process all items in one batch when count < 50."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        resp = _mock_response(result=[100, 101, 102])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.create_cards_batch(items, recording_progress)

        assert result == [100, 101, 102]

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

        assert result == list(range(150))
        # Exactly 2 batches (100 + 50), not more
        assert mock_post.call_count == 2

    def test_short_addnotes_array_raises(self, test_config, make_tokenized_word):
        """A note_ids list shorter than the batch (malformed addNotes response)
        must raise a typed AnkiConnectionError, not silently under-merge —
        length alignment is load-bearing for the positional zip."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        short_resp = _mock_response(result=[100, 101])  # 2 ids for 3 notes

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=short_resp),
            pytest.raises(AnkiConnectionError, match="addNotes"),
        ):
            service.create_cards_batch(items)

    def test_mistyped_addnotes_slot_raises(self, test_config, make_tokenized_word):
        """A non-int, non-null slot in the addNotes result is malformed and
        must raise with the offending index, not be counted as a creation."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=2)

        bad_resp = _mock_response(result=[100, "oops"])

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=bad_resp),
            pytest.raises(AnkiConnectionError, match="index 1"),
        ):
            service.create_cards_batch(items)

    def test_addnotes_returns_only_confirmed_note_ids(self, test_config, make_tokenized_word):
        """Return confirmed identities; null addNotes slots stay skipped."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=5)

        # 3 out of 5 succeed (2 are null / duplicates)
        resp = _mock_response(result=[100, None, 102, None, 104])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.create_cards_batch(items)

        assert result == [100, 102, 104]
        assert service.last_created_lemmas == ["word_0", "word_2", "word_4"]

    def test_duplicate_in_second_note_chunk_never_uploads_its_media(self, test_config, make_tokenized_word):
        service = AnkiService(test_config)
        items = []
        for index in range(100):
            expression = f"語{index}"
            word = make_tokenized_word(surface=expression, lemma=expression, pos="名詞")
            items.append(
                CardPayload(
                    word=word,
                    media=MediaData(screenshot_filename=f"shot-{index}.jpg"),
                    definition=f"definition {index}",
                )
            )
        repeated = make_tokenized_word(surface="語0", lemma="語0", pos="名詞")
        repeated_payload = CardPayload(
            word=repeated,
            media=MediaData(screenshot_filename="repeated.jpg"),
            definition="repeated",
        )
        items.append(repeated_payload)

        created_fields: set[str] = set()
        stored_batches: list[list[CardPayload]] = []
        next_id = 1

        def probe(notes):
            return [next(iter(note["fields"].values())) in created_fields for note in notes]

        def store(batch):
            stored_batches.append(list(batch))
            return set()

        def add_notes(_url, action, *, params, timeout):
            nonlocal next_id
            assert action == "addNotes"
            note_ids = []
            for note in params["notes"]:
                first_field = next(iter(note["fields"].values()))
                if first_field in created_fields:
                    note_ids.append(None)
                    continue
                created_fields.add(first_field)
                note_ids.append(next_id)
                next_id += 1
            return note_ids

        with (
            patch.object(service, "_probe_duplicates", side_effect=probe),
            patch.object(service, "_store_media_files_batch", side_effect=store),
            patch.object(service, "_upload_dict_media_batch"),
            patch("anki_miner.services.anki_service.post_action", side_effect=add_notes),
        ):
            assert len(service.create_cards_batch(items)) == 100

        assert [len(batch) for batch in stored_batches] == [100]
        assert all(repeated_payload not in batch for batch in stored_batches)
        assert service.last_skipped_duplicates == 1

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
        vocab cache is merged (not wiped) with the batch's mined_forms so the
        filter stays accurate, while the error still propagates to the pipeline
        boundary."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=150)  # 2 batches (100 + 50)

        # Prime the vocab cache.
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
        # Cache stays populated (not None) and the batch's mined_form is merged in.
        # _make_word_data creates words with surface="食べる" (default), so all
        # items have mined_form="食べる" (pos=None → surface).
        assert service._existing_vocab_cache is not None
        assert "食べる" in service._existing_vocab_cache
        assert "既知" in service._existing_vocab_cache

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

        # on_progress called once (one batch), reporting the cumulative total
        assert len(recording_progress.progresses) == 1
        assert recording_progress.progresses[0][1] == "Cards created: 3/3"

        # on_complete called once
        assert recording_progress.completes == 1

        # no errors
        assert len(recording_progress.errors) == 0

    def test_progress_label_is_cumulative_across_batches(self, test_config, make_tokenized_word, recording_progress):
        """Regression (misleading "Cards created: 100/100"): the per-update
        label reports the CUMULATIVE run total, never per-chunk figures."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=250)

        responses = [
            _mock_response(result=list(range(100))),
            _mock_response(result=list(range(100, 200))),
            _mock_response(result=list(range(200, 250))),
        ]

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=responses):
            service.create_cards_batch(items, recording_progress)

        labels = [label for _, label in recording_progress.progresses]
        assert labels == [
            "Cards created: 100/250",
            "Cards created: 200/250",
            "Cards created: 250/250",
        ]

    def test_progress_label_cumulative_excludes_null_slots(self, test_config, make_tokenized_word, recording_progress):
        """Null addNotes slots (duplicates) reduce the cumulative count."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=150)

        responses = [
            _mock_response(result=[*range(60), *([None] * 40)]),
            _mock_response(result=list(range(60, 110))),
        ]

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=responses):
            service.create_cards_batch(items, recording_progress)

        labels = [label for _, label in recording_progress.progresses]
        assert labels == [
            "Cards created: 60/150",
            "Cards created: 110/150",
        ]

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

        assert len(result) == 1
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

        assert len(result) == 1
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

    def test_excluded_deck_admission_allows_anki_duplicate_but_deduplicates_run(self, test_config, make_tokenized_word):
        """A word absent from the exclusion-aware vocabulary may be re-carded once."""
        from dataclasses import replace

        config = replace(test_config, excluded_decks=("Archive",))
        service = AnkiService(config)
        service._existing_vocab_cache = set()
        word = make_tokenized_word(surface="猫", lemma="猫", sentence="猫だ。", pos="名詞")
        items = [
            CardPayload(word=word, media=MediaData(), definition="cat"),
            CardPayload(word=word, media=MediaData(), definition="cat again"),
        ]

        with (
            patch.object(service, "_store_media_files_batch", return_value=set()),
            patch.object(service, "_upload_dict_media_batch"),
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=[[{"canAdd": True, "error": None}], [10]],
            ) as post,
        ):
            assert service.create_cards_batch(items) == [10]

        assert [call.args[1] for call in post.call_args_list] == ["canAddNotesWithErrorDetail", "addNotes"]
        validation_note = post.call_args_list[0].kwargs["params"]["notes"][0]
        assert validation_note["options"]["allowDuplicate"] is True
        submitted = post.call_args_list[1].kwargs["params"]["notes"]
        assert len(submitted) == 1
        assert submitted[0]["options"] == {"allowDuplicate": True}
        assert service.last_skipped_duplicates == 1

    def test_excluded_deck_invalid_note_fails_before_media_upload(self, test_config, make_tokenized_word):
        from dataclasses import replace

        service = AnkiService(replace(test_config, excluded_decks=("Archive",)))
        service._existing_vocab_cache = set()
        invalid = make_tokenized_word(surface="", lemma="", pos="名詞")

        with (
            patch.object(service, "_store_media_files_batch", return_value=set()) as store_media,
            patch.object(service, "_upload_dict_media_batch") as upload_dict_media,
            patch(
                "anki_miner.services.anki_service.post_action",
                return_value=[{"canAdd": False, "error": "first field must not be empty"}],
            ),
            pytest.raises(AnkiConnectionError, match="first field must not be empty"),
        ):
            service.create_cards_batch([CardPayload(word=invalid, media=MediaData(), definition="invalid")])

        store_media.assert_not_called()
        upload_dict_media.assert_not_called()

    def test_excluded_deck_admission_fails_closed_when_filtered_vocab_is_uncertain(
        self, test_config, make_tokenized_word
    ):
        """A degraded exclusion query must not authorize collection-wide duplicates."""
        from dataclasses import replace

        service = AnkiService(replace(test_config, excluded_decks=("Archive",)))
        word = make_tokenized_word(surface="猫", lemma="猫", sentence="猫だ。", pos="名詞")
        timeout_error = AnkiConnectionError("timed out")
        timeout_error.__cause__ = requests.exceptions.Timeout("slow")

        with (
            patch("anki_miner.services.anki_service.post_action", side_effect=[timeout_error, [10]]),
            patch.object(service, "_store_media_files_batch", return_value=set()) as store_media,
            patch.object(service, "_upload_dict_media_batch") as upload_dict_media,
            pytest.raises(AnkiConnectionError, match="timed out"),
        ):
            service.create_cards_batch([CardPayload(word=word, media=MediaData(), definition="cat")])

        store_media.assert_not_called()
        upload_dict_media.assert_not_called()

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
# TestVocabCacheMergeOnCreate (OVH-052)
# ---------------------------------------------------------------------------


class TestVocabCacheMergeOnCreate:
    """create_cards_batch merges new mined_forms into the cache instead of wiping it
    (OVH-052: incremental merge so episodes 2..N get a cache hit, not a full rescan).
    """

    def _make_japanese_word_data(self, make_tokenized_word, surface="食べる", n=1):
        """Helper producing payloads whose mined_form is a Japanese string."""
        items = []
        for i in range(n):
            word = make_tokenized_word(surface=surface, lemma=surface, pos=None)
            media = MediaData()
            items.append(CardPayload(word=word, media=media, definition=f"def_{i}"))
        return items

    def test_cache_populated_and_contains_new_mined_form_after_create(self, test_config, make_tokenized_word):
        """After get_existing_vocabulary populates the cache and create_cards_batch
        creates cards, the cache must be POPULATED (not None) and CONTAIN the
        newly carded mined_form."""
        service = AnkiService(test_config)

        # Populate the cache via get_existing_vocabulary
        find_resp = _mock_response(result=[1])
        notes_resp = _mock_response(result=[{"fields": {"word": {"value": "既知"}}}])
        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
            vocab = service.get_existing_vocabulary()
        assert "既知" in vocab
        assert service._existing_vocab_cache is not None

        # Create a card for 食べる
        items = self._make_japanese_word_data(make_tokenized_word, surface="食べる")
        add_resp = _mock_response(result=[100])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=add_resp):
            created = service.create_cards_batch(items)

        assert len(created) == 1
        # Cache must still be populated (not wiped) and contain the new word
        assert service._existing_vocab_cache is not None
        assert "食べる" in service._existing_vocab_cache
        assert "既知" in service._existing_vocab_cache

    def test_no_full_rescan_after_create_when_cache_populated(self, test_config, make_tokenized_word):
        """get_existing_vocabulary called after a create must NOT re-query AnkiConnect
        (the merge keeps the cache populated so the fast-path is taken)."""
        service = AnkiService(test_config)

        # Populate the cache
        find_resp = _mock_response(result=[1])
        notes_resp = _mock_response(result=[{"fields": {"word": {"value": "既知"}}}])
        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[find_resp, notes_resp]):
            service.get_existing_vocabulary()

        # Create a card
        items = self._make_japanese_word_data(make_tokenized_word, surface="食べる")
        add_resp = _mock_response(result=[100])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=add_resp):
            service.create_cards_batch(items)

        # Second get_existing_vocabulary — no HTTP call should happen (cache hit)
        with patch("anki_miner.services._ankiconnect.requests.post") as mock_post:
            vocab = service.get_existing_vocabulary()

        mock_post.assert_not_called()
        assert "食べる" in vocab
        assert "既知" in vocab

    def test_cache_stays_none_when_unpopulated_before_create(self, test_config, make_tokenized_word):
        """When the cache has never been populated (None), a create must leave it
        None — the next get_existing_vocabulary scans normally."""
        service = AnkiService(test_config)
        assert service._existing_vocab_cache is None

        items = self._make_japanese_word_data(make_tokenized_word, surface="食べる")
        add_resp = _mock_response(result=[100])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=add_resp):
            service.create_cards_batch(items)

        # Cache must still be None; next call will do a full scan
        assert service._existing_vocab_cache is None

    def test_null_slot_word_not_merged_into_cache(self, test_config, make_tokenized_word):
        """Only CREATED words are merged into the cache — a word addNotes returned a
        null slot for must NOT be merged. Merging a non-duplicate silent rejection
        (bad model/field) would wrongly mark a not-in-collection word 'known' and
        filter it out of later batch items (F10)."""
        from anki_miner.models import CardPayload, MediaData

        service = AnkiService(test_config)
        service._existing_vocab_cache = {"既知"}

        created = make_tokenized_word(surface="食べる", lemma="食べる", pos=None)
        rejected = make_tokenized_word(surface="未作成", lemma="未作成", pos=None)
        items = [
            CardPayload(word=created, media=MediaData(), definition="d1"),
            CardPayload(word=rejected, media=MediaData(), definition="d2"),
        ]

        # addNotes creates the first (id 200) and returns a null slot for the second.
        add_resp = _mock_response(result=[200, None])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=add_resp):
            n = service.create_cards_batch(items)

        assert len(n) == 1
        assert "食べる" in service._existing_vocab_cache, "created word must be merged"
        assert "未作成" not in service._existing_vocab_cache, "uncreated word must NOT be merged"
        assert "既知" in service._existing_vocab_cache

    def test_delete_notes_still_invalidates_cache(self, test_config, make_tokenized_word):
        """delete_notes (undo path) must still wipe the cache completely."""
        service = AnkiService(test_config)
        service._existing_vocab_cache = {"食べる", "既知"}

        del_resp = _mock_response(result=None)
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=del_resp):
            service.delete_notes([100])

        assert service._existing_vocab_cache is None

    def test_merged_values_match_get_existing_vocabulary_normalization(self, test_config, make_tokenized_word):
        """The merged key must be _strip_for_dedup(mined_form) — identical to what
        get_existing_vocabulary would return for a note whose first field is that
        mined_form, so dedup semantics are unchanged."""
        from anki_miner.services.anki_service import _strip_for_dedup

        service = AnkiService(test_config)
        service._existing_vocab_cache = set()

        # Use a surface with markup-like content that _strip_for_dedup normalizes
        surface = "食べる"
        items = self._make_japanese_word_data(make_tokenized_word, surface=surface)
        add_resp = _mock_response(result=[1])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=add_resp):
            service.create_cards_batch(items)

        expected_key = _strip_for_dedup(surface)
        assert expected_key in service._existing_vocab_cache


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

        # multi response: two non-error sub-results (one per file)
        resp = _mock_response(result=[None, None])
        ss_hashed = _content_addressed_name("shot.jpg", b"screenshot-data")
        au_hashed = _content_addressed_name("clip.mp3", b"audio-data")

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            stored = service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

        # One batched POST via multi action
        assert mock_post.call_count == 1
        payload = mock_post.call_args[1]["json"]
        assert payload["action"] == "multi"

        # Files are sent (and stored) under their content-hashed names (7.5).
        filenames_sent = [a["params"]["filename"] for a in payload["params"]["actions"]]
        assert ss_hashed in filenames_sent
        assert au_hashed in filenames_sent
        assert stored == {ss_hashed, au_hashed}
        # The hashed names are propagated back onto the payload's MediaData.
        assert media.screenshot_filename == ss_hashed
        assert media.audio_filename == au_hashed

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

        with patch("anki_miner.services._ankiconnect.requests.post") as mock_post:
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

        # 3 cards × 2 files = 6 non-error sub-results (all successful)
        multi_resp = _mock_response(result=[None] * 6)

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=multi_resp) as mock_post:
            stored = service._store_media_files_batch(items)

        # One POST (all 6 files fit in a single chunk of ≤50)
        assert mock_post.call_count == 1
        payload = mock_post.call_args[1]["json"]
        assert payload["action"] == "multi"
        assert len(payload["params"]["actions"]) == 6

        # All files stored under their content-hashed names
        expected = {_content_addressed_name(f"shot_{i}.jpg", b"screenshot-data") for i in range(3)} | {
            _content_addressed_name(f"clip_{i}.mp3", b"audio-data") for i in range(3)
        }
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

    def test_length_mismatch_retries_each_file(self, test_config, make_tokenized_word, tmp_path, caplog):
        """A truncated multi response confirms nothing; retry each file."""
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
        responses = [
            _mock_response(result=["shot.jpg"]),
            _mock_response(result="shot.jpg"),
            _mock_response(result="clip.mp3"),
        ]

        with (
            patch("anki_miner.services._ankiconnect.requests.post", side_effect=responses),
            caplog.at_level(logging.WARNING, logger="anki_miner.services.anki_media_store"),
        ):
            stored = service._store_media_files_batch([CardPayload(word=word, media=media, definition="def")])

        assert any("retrying 2 file" in r.message for r in caplog.records)
        assert stored == {"shot.jpg", "clip.mp3"}

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
        assert stored == {_content_addressed_name(f"shot_{i}.jpg", b"x" * 300) for i in range(3)}

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

        # And shipped once in the multi POST (under its content-hashed name)
        cover_hashed = _content_addressed_name("cover.jpg", b"cover-data")
        payload = mock_post.call_args[1]["json"]
        filenames_sent = [a["params"]["filename"] for a in payload["params"]["actions"]]
        assert filenames_sent.count(cover_hashed) == 1
        assert stored == {cover_hashed} | {_content_addressed_name(f"clip_{i}.mp3", b"audio-data") for i in range(3)}

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
        from dataclasses import replace

        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
        )
        service = AnkiService(config)

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
        service = AnkiService(self._config_with_expression_audio(test_config))

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

        resp = _mock_response(result=[None, None])
        clip_hashed = _content_addressed_name("clip.mp3", b"audio-data")
        exp_hashed = _content_addressed_name("食べる_exp.mp3", b"expression-audio-data")

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            stored = service._store_media_files_batch(
                [CardPayload(word=make_tokenized_word(), media=media, definition="def")]
            )

        payload = mock_post.call_args[1]["json"]
        filenames_sent = [a["params"]["filename"] for a in payload["params"]["actions"]]
        assert exp_hashed in filenames_sent
        assert stored == {clip_hashed, exp_hashed}
        assert media.expression_audio_filename == exp_hashed

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
        service = AnkiService(self._config_with_expression_audio(test_config))

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

        resp = _mock_response(result=[None])
        exp_hashed = _content_addressed_name("食べる_exp.mp3", b"expression-audio-data")

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            stored = service._store_media_files_batch(items)

        payload = mock_post.call_args[1]["json"]
        filenames_sent = [a["params"]["filename"] for a in payload["params"]["actions"]]
        assert filenames_sent == [exp_hashed]
        assert stored == {exp_hashed}
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

    def test_addnotes_and_multi_cardinality(self):
        """A truncated multi result is malformed, never implicit success."""
        actions = [
            {"action": "updateNoteFields", "version": 6, "params": {"note": {"id": 1, "fields": {}}}},
            {"action": "updateNoteFields", "version": 6, "params": {"note": {"id": 2, "fields": {}}}},
        ]
        resp = _mock_response(result=[None])

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            pytest.raises(AnkiConnectionError, match="1 item.*expected 2"),
        ):
            post_multi("http://localhost:8765", actions)


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

        assert len(result) == 1
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

        definition = '<div>ふ<img class="anki-miner-dict-media" src="test-dict__svg-accent_X.svg">そ</div>'
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
        assert len(created) == 2
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
        assert len(created) == 1
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

        assert len(count) == 1
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

    _GLOSSARY_HTML = '<div class="yomitan-glossary"><ol><li data-dictionary="X">X def</li></ol></div>'

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

        assert len(result) == 1
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

        assert len(result) == 1
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

        assert len(result) == 1
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
            '<div class="yomitan-glossary"><img class="anki-miner-dict-media" src="test-dict__svg-pitch_X.svg"></div>'
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

        assert len(result) == 1
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

        assert len(result) == 1

    def test_none_extra_fields_does_not_crash(self, test_config, make_tokenized_word):
        """CardPayload with default extra_fields=None must still work fine after glossary wiring."""
        service = AnkiService(test_config)
        word = make_tokenized_word()
        media = MediaData()

        resp = _mock_response(result=[123])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.create_cards_batch([CardPayload(word=word, media=media, definition="def")])

        assert len(result) == 1


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
            side_effect=[[1], [{"fields": {"word": {"value": "食べる"}}}]],
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

    def test_create_cards_batch_merges_on_success(self, test_config, make_tokenized_word):
        """create_cards_batch must MERGE (not wipe) the cache when cards are created.

        The cache stays populated and the new mined_form is union-ed in so that
        subsequent episodes in the same session hit the cache instead of re-scanning
        the whole collection (OVH-052).
        """
        service = AnkiService(test_config)
        # Warm the cache with an existing word
        service._existing_vocab_cache = {"既知"}
        assert service._existing_vocab_cache is not None

        # make_tokenized_word defaults: surface="食べる", pos=None → mined_form="食べる"
        word = make_tokenized_word()
        media = MediaData()
        resp = _mock_response(result=[12345])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            service.create_cards_batch([CardPayload(word=word, media=media, definition="def")])

        # Cache is still populated (not None) and contains both the pre-existing
        # word and the newly created one.
        assert service._existing_vocab_cache is not None
        assert "既知" in service._existing_vocab_cache
        assert "食べる" in service._existing_vocab_cache

    def test_create_cards_batch_no_invalidation_when_zero_created(self, test_config, make_tokenized_word):
        """Cache must NOT be invalidated when all note IDs come back null (zero created)."""
        service = AnkiService(test_config)
        service._existing_vocab_cache = {"食べる"}

        word = make_tokenized_word()
        media = MediaData()
        # Single note, its slot null → total_created == 0
        resp = _mock_response(result=[None])

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
# TestFrequencyFieldRouting (Multiple Additive Frequency Sources)
# ---------------------------------------------------------------------------


class TestFrequencyFieldRouting:
    """Tests for the multi-source frequency fields on the built note.

    ``frequency`` is now a pre-rendered bullet list (``<ul><li>…``) and must be
    inserted UNESCAPED (like glossary); ``frequency_sort`` is a bare number that
    rides the normal escaped optional pass. Both follow the optional gating
    contract: written only when their ``anki_fields`` mapping is non-empty AND
    the value is non-empty.
    """

    _FREQ_HTML = "<ul><li>BCCWJ: 2496</li></ul>"

    def _config(self, test_config, *, frequency="Frequency", frequency_sort="FrequencySort"):
        from dataclasses import replace

        return replace(
            test_config,
            anki_fields={
                **test_config.anki_fields,
                "frequency": frequency,
                "frequency_sort": frequency_sort,
            },
        )

    def test_frequency_inserted_unescaped(self, test_config, make_tokenized_word):
        """The rendered bullet HTML reaches the mapped field with literal tags."""
        from anki_miner.services.anki_note_builder import build_note

        config = self._config(test_config)
        item = CardPayload(
            word=make_tokenized_word(),
            media=MediaData(),
            definition="def",
            extra_fields={"frequency": self._FREQ_HTML, "frequency_sort": "2496"},
        )

        built = build_note(item, config, stored_files=set())

        assert built.note["fields"]["Frequency"] == self._FREQ_HTML
        assert "<ul>" in built.note["fields"]["Frequency"]
        assert "&lt;" not in built.note["fields"]["Frequency"]

    def test_frequency_sort_inserted_as_number(self, test_config, make_tokenized_word):
        """frequency_sort lands as the bare number in its mapped field."""
        from anki_miner.services.anki_note_builder import build_note

        config = self._config(test_config)
        item = CardPayload(
            word=make_tokenized_word(),
            media=MediaData(),
            definition="def",
            extra_fields={"frequency": self._FREQ_HTML, "frequency_sort": "2496"},
        )

        built = build_note(item, config, stored_files=set())

        assert built.note["fields"]["FrequencySort"] == "2496"

    def test_fields_omitted_when_mapping_empty(self, test_config, make_tokenized_word):
        """Blank mappings → neither field appears on the note."""
        from anki_miner.services.anki_note_builder import build_note

        config = self._config(test_config, frequency="", frequency_sort="")
        item = CardPayload(
            word=make_tokenized_word(),
            media=MediaData(),
            definition="def",
            extra_fields={"frequency": self._FREQ_HTML, "frequency_sort": "2496"},
        )

        built = build_note(item, config, stored_files=set())

        assert "Frequency" not in built.note["fields"]
        assert "FrequencySort" not in built.note["fields"]
        # The raw HTML must not leak into any other field either.
        assert self._FREQ_HTML not in built.note["fields"].values()

    def test_fields_omitted_when_value_empty(self, test_config, make_tokenized_word):
        """Mapped but empty values → fields stay off the note (gating contract)."""
        from anki_miner.services.anki_note_builder import build_note

        config = self._config(test_config)
        item = CardPayload(
            word=make_tokenized_word(),
            media=MediaData(),
            definition="def",
            extra_fields={"frequency": "", "frequency_sort": ""},
        )

        built = build_note(item, config, stored_files=set())

        assert "Frequency" not in built.note["fields"]
        assert "FrequencySort" not in built.note["fields"]

    def test_frequency_sort_in_optional_field_keys(self):
        """frequency_sort must be a recognized optional field key (escaped pass)."""
        from anki_miner.services.anki_note_builder import OPTIONAL_FIELD_KEYS, REQUIRED_FIELD_KEYS

        assert "frequency_sort" in OPTIONAL_FIELD_KEYS
        assert "frequency_sort" not in REQUIRED_FIELD_KEYS


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


class TestGetModelNames:
    """Tests for AnkiService.get_model_names (Task 3 setup wizard)."""

    def test_returns_model_list(self, test_config):
        """Should return the modelNames result as a list."""
        service = AnkiService(test_config)
        resp = _mock_response(result=["Basic", "Lapis", "Cloze"])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            assert service.get_model_names() == ["Basic", "Lapis", "Cloze"]
        assert mock_post.call_args[1]["json"]["action"] == "modelNames"

    def test_none_result_returns_empty(self, test_config):
        """Should coerce a None result to an empty list."""
        service = AnkiService(test_config)
        resp = _mock_response(result=None)
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            assert service.get_model_names() == []

    def test_connection_error_returns_empty(self, test_config):
        """Should swallow AnkiConnectionError and return an empty list."""
        service = AnkiService(test_config)
        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=AnkiConnectionError("down"),
        ):
            assert service.get_model_names() == []


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

    def test_happy_path_checks_deck_after_checks(self, test_config):
        """Should verify the deck via deckNames after modelNames + modelFieldNames."""

        service = AnkiService(test_config)
        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[self._MODELS, self._FIELDS, ["Default", test_config.anki_deck_name]],
        ) as mock_pa:
            service.verify_card_target()

        actions = [call[0][1] for call in mock_pa.call_args_list]
        assert actions == ["modelNames", "modelFieldNames", "deckNames"]
        assert "createDeck" not in actions

    def test_deck_missing_raises_setup_error(self, test_config):
        """A deck absent from Anki must fail the run, not be created."""
        service = AnkiService(test_config)
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=[self._MODELS, self._FIELDS, ["Default", "Other"]],
            ) as mock_pa,
            pytest.raises(SetupError, match="test_deck"),
        ):
            service.verify_card_target()

        actions = [call[0][1] for call in mock_pa.call_args_list]
        assert actions == ["modelNames", "modelFieldNames", "deckNames"]
        assert "createDeck" not in actions

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

    def test_word_mapping_must_target_first_model_field(self, test_config):
        """The mined word must map to the note type's ordered first field."""
        from dataclasses import replace

        mappings = dict.fromkeys(test_config.anki_fields, "")
        mappings.update(word="Back", sentence="Front")
        config = replace(test_config, anki_fields=mappings)
        service = AnkiService(config)

        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=[self._MODELS, ["Front", "Back"], [config.anki_deck_name]],
            ),
            pytest.raises(SetupError, match="first field.*Front"),
        ):
            service.verify_card_target()

    def test_duplicate_nonempty_field_targets_raise_setup_error(self, test_config):
        """Two miner fields cannot target the same Anki note field."""
        from dataclasses import replace

        mappings = dict.fromkeys(test_config.anki_fields, "")
        mappings.update(word="Expression", sentence="Expression")
        config = replace(test_config, anki_fields=mappings)
        service = AnkiService(config)

        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=[self._MODELS, ["Expression"], [config.anki_deck_name]],
            ) as mock_pa,
            pytest.raises(SetupError, match="Expression.*mapped more than once"),
        ):
            service.verify_card_target()

        actions = [call[0][1] for call in mock_pa.call_args_list]
        assert actions == ["modelNames"]

    def test_active_card_type_marker_collision_raises_setup_error(self, test_config):
        """The active card-type marker writes "x"; sharing a mapped target must fail preflight."""
        from dataclasses import replace

        word_target = test_config.anki_fields["word"]
        config = replace(
            test_config,
            card_type="sentence",
            card_type_marker_fields={"sentence": word_target},
        )
        service = AnkiService(config)

        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=[self._MODELS, self._FIELDS, [config.anki_deck_name]],
            ) as mock_pa,
            pytest.raises(SetupError, match=f"{word_target}.*mapped more than once"),
        ):
            service.verify_card_target()

        actions = [call[0][1] for call in mock_pa.call_args_list]
        assert actions == ["modelNames"]

    def test_inactive_card_type_marker_collision_allowed(self, test_config):
        """Markers for inactive card types are never written and may collide."""
        from dataclasses import replace

        config = replace(
            test_config,
            card_type="",
            card_type_marker_fields={"sentence": test_config.anki_fields["word"]},
        )
        service = AnkiService(config)
        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[self._MODELS, self._FIELDS, ["Default", config.anki_deck_name]],
        ):
            service.verify_card_target()  # must not raise

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
            side_effect=[self._MODELS, self._FIELDS, ["Default", test_config.anki_deck_name]],
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

    def test_model_field_names_connection_error_propagates(self, test_config):
        """A modelFieldNames failure must not become a field-mapping SetupError."""
        service = AnkiService(test_config)
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=[self._MODELS, AnkiConnectionError("field lookup failed")],
            ),
            pytest.raises(AnkiConnectionError, match="field lookup failed"),
        ):
            service.verify_card_target()

    def test_active_card_type_marker_validated_pass(self, test_config):
        """When a card type is active, its marker field must exist on the note type."""
        from dataclasses import replace

        config = replace(test_config, card_type="click")  # default marker → IsClickCard
        service = AnkiService(config)
        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[self._MODELS, [*self._FIELDS, "IsClickCard"], ["Default", test_config.anki_deck_name]],
        ):
            service.verify_card_target()  # must not raise

    def test_active_card_type_marker_missing_raises(self, test_config):
        """A configured card type whose marker field is absent must raise SetupError."""
        from dataclasses import replace

        config = replace(test_config, card_type="click")
        service = AnkiService(config)
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=[self._MODELS, self._FIELDS],  # no IsClickCard
            ),
            pytest.raises(SetupError, match="IsClickCard"),
        ):
            service.verify_card_target()

    def test_inactive_card_type_markers_ignored(self, test_config):
        """Only the active marker is required; the other three are never validated."""
        from dataclasses import replace

        config = replace(test_config, card_type="word_and_sentence")
        service = AnkiService(config)
        # Note type has the active marker but NOT the three inactive ones.
        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[
                self._MODELS,
                [*self._FIELDS, "IsWordAndSentenceCard"],
                ["Default", test_config.anki_deck_name],
            ],
        ):
            service.verify_card_target()  # must not raise


class TestCardTypeMarker:
    """build_note stamps an 'x' into exactly the active card-type marker field."""

    def test_active_marker_stamped(self, test_config, make_tokenized_word):
        """An active card type writes 'x' into its marker field and no other."""
        from dataclasses import replace

        from anki_miner.services.anki_note_builder import build_note

        config = replace(test_config, card_type="word_and_sentence")
        item = CardPayload(word=make_tokenized_word(), media=MediaData(), definition="def")

        fields = build_note(item, config, stored_files=set()).note["fields"]

        assert fields["IsWordAndSentenceCard"] == "x"
        for inactive in ("IsClickCard", "IsSentenceCard", "IsAudioCard"):
            assert inactive not in fields

    def test_no_marker_when_disabled(self, test_config, make_tokenized_word):
        """The default (card_type='') writes none of the four marker fields."""
        from anki_miner.services.anki_note_builder import build_note

        assert test_config.card_type == ""
        item = CardPayload(word=make_tokenized_word(), media=MediaData(), definition="def")

        fields = build_note(item, test_config, stored_files=set()).note["fields"]

        for name in ("IsWordAndSentenceCard", "IsClickCard", "IsSentenceCard", "IsAudioCard"):
            assert name not in fields

    def test_renamed_marker_field_honored(self, test_config, make_tokenized_word):
        """A fork that renames the marker field still gets the 'x' in its name."""
        from dataclasses import replace

        from anki_miner.services.anki_note_builder import build_note

        config = replace(
            test_config,
            card_type="click",
            card_type_marker_fields={**test_config.card_type_marker_fields, "click": "MyClickMarker"},
        )
        item = CardPayload(word=make_tokenized_word(), media=MediaData(), definition="def")

        fields = build_note(item, config, stored_files=set()).note["fields"]

        assert fields["MyClickMarker"] == "x"
        assert "IsClickCard" not in fields


# ---------------------------------------------------------------------------
# TestNullSlotSkipCount (OVH-040)
# ---------------------------------------------------------------------------


class TestNullSlotSkipCount:
    """Residual addNotes null slots are counted and surfaced honestly (OVH-040).

    Duplicates are now caught by the pre-add probe (see ``TestProbeDuplicates``);
    these tests run with the probe stubbed to "all addable", so any ``null`` slot
    in the addNotes result is a residual rejection the probe had cleared. Such a
    slot must still be counted in ``last_skipped_duplicates`` and surfaced via the
    INFO log ("note(s) were not created (likely already in your collection)") so
    a created-vs-submitted gap is never silent.
    """

    def _make_word_data(self, make_tokenized_word, n=1):
        items = []
        for i in range(n):
            word = make_tokenized_word(lemma=f"word_{i}")
            items.append(CardPayload(word=word, media=MediaData(), definition=f"def_{i}"))
        return items

    def test_null_slots_counted_in_last_skipped_duplicates(self, test_config, make_tokenized_word):
        """addNotes returning some null slots must fold them into last_skipped_duplicates."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=5)

        # 3 created, 2 null (silent rejections)
        resp = _mock_response(result=[100, None, 102, None, 104])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.create_cards_batch(items)

        assert result == [100, 102, 104]
        assert service.last_skipped_duplicates == 2

    def test_null_slots_emit_info_log(self, test_config, make_tokenized_word, caplog):
        """At least one null slot must emit an INFO log with the approved wording."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        resp = _mock_response(result=[100, None, 102])

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            caplog.at_level(logging.INFO, logger="anki_miner.services.anki_service"),
        ):
            service.create_cards_batch(items)

        messages = " ".join(r.message for r in caplog.records)
        assert "were not created" in messages
        assert "likely already in your collection" in messages

    def test_all_success_batch_reports_zero_skips(self, test_config, make_tokenized_word):
        """When all notes succeed (no null slots), last_skipped_duplicates must be 0."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        resp = _mock_response(result=[100, 101, 102])

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            result = service.create_cards_batch(items)

        assert result == [100, 101, 102]
        assert service.last_skipped_duplicates == 0

    def test_null_slots_accumulated_across_multiple_batches(self, test_config, make_tokenized_word):
        """Null slots from multiple addNotes batches are summed into one counter."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=200)  # two 100-note batches

        # Batch 1: 99 created, 1 null; batch 2: 98 created, 2 nulls
        batch1_ids = list(range(99)) + [None]
        batch2_ids = list(range(99, 197)) + [None, None]
        batch1_resp = _mock_response(result=batch1_ids)
        batch2_resp = _mock_response(result=batch2_ids)

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[batch1_resp, batch2_resp],
        ):
            result = service.create_cards_batch(items)

        assert result == list(range(197))
        assert service.last_skipped_duplicates == 3


class TestProbeDuplicates:
    """The pre-add duplicate probe (Yomitan partitionAddibleNotes port).

    These tests run the REAL ``_probe_duplicates`` (marked ``real_probe`` to opt
    out of the module autouse stub) and drive it through ``create_cards_batch``
    against mocked ``canAddNotesWithErrorDetail`` / ``canAddNotes`` responses.
    """

    pytestmark = pytest.mark.real_probe

    @pytest.fixture(autouse=True)
    def _no_retry_delay(self, monkeypatch):
        """Zero the probe-retry backoff: the transport-error test would
        otherwise sit out two real 8s waits before propagating."""
        monkeypatch.setattr("anki_miner.services.anki_service._PROBE_RETRY_DELAY_S", 0.0)

    def _make_word_data(self, make_tokenized_word, n=1):
        items = []
        for i in range(n):
            word = make_tokenized_word(lemma=f"word_{i}")
            items.append(CardPayload(word=word, media=MediaData(), definition=f"def_{i}"))
        return items

    @staticmethod
    def _actions(mock_post):
        return [c[1]["json"]["action"] for c in mock_post.call_args_list]

    def test_empty_notes_probe_makes_no_request(self, test_config):
        """The probe short-circuits an empty batch without touching AnkiConnect."""
        service = AnkiService(test_config)
        with patch("anki_miner.services._ankiconnect.requests.post") as mock_post:
            assert service._probe_duplicates([]) == []
        mock_post.assert_not_called()

    def test_strip_note_to_first_field_keeps_only_first(self):
        """First-field-only clone (Yomitan _stripNotesArray); original untouched."""
        note = {
            "deckName": "D",
            "modelName": "M",
            "tags": ["t"],
            "fields": {"Expression": "食べる", "Meaning": "eat", "Sentence": "彼は食べる"},
            "options": {"allowDuplicate": True},
        }
        stripped = AnkiService._strip_note_to_first_field(note)
        assert stripped["fields"] == {"Expression": "食べる"}
        assert stripped["deckName"] == "D"
        assert stripped["options"] == {"allowDuplicate": True}
        # Source note is not mutated.
        assert set(note["fields"]) == {"Expression", "Meaning", "Sentence"}

    def test_probe_skips_duplicates_and_submits_rest(self, test_config, make_tokenized_word):
        """Probe-flagged duplicates are skipped; only the rest reach addNotes."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=3)

        # note 0 addable, note 1 duplicate, note 2 addable.
        probe = _mock_response(
            result=[
                {"canAdd": True, "error": None},
                {"canAdd": False, "error": "cannot create note because it is a duplicate"},
                {"canAdd": True, "error": None},
            ]
        )
        add = _mock_response(result=[100, 102])

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[probe, add]) as mock_post:
            result = service.create_cards_batch(items)

        assert result == [100, 102]
        assert service.last_skipped_duplicates == 1
        assert service.last_created_note_ids == [100, 102]
        assert self._actions(mock_post) == ["canAddNotesWithErrorDetail", "addNotes"]
        # addNotes received only the 2 non-duplicates.
        add_call = mock_post.call_args_list[1]
        assert len(add_call[1]["json"]["params"]["notes"]) == 2

    def test_all_duplicates_skips_addnotes_entirely(self, test_config, make_tokenized_word):
        """When every note is a duplicate, no addNotes request is made."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=2)

        probe = _mock_response(
            result=[
                {"canAdd": False, "error": "cannot create note because it is a duplicate"},
                {"canAdd": False, "error": "cannot create note because it is a duplicate"},
            ]
        )

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[probe]) as mock_post:
            result = service.create_cards_batch(items)

        assert result == []
        assert service.last_skipped_duplicates == 2
        assert self._actions(mock_post) == ["canAddNotesWithErrorDetail"]

    def test_probe_strips_to_first_field_and_flips_allow_duplicate(self, test_config, make_tokenized_word):
        """Probe clones carry only the first field and allowDuplicate=False."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=1)

        probe = _mock_response(result=[{"canAdd": True, "error": None}])
        add = _mock_response(result=[100])

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[probe, add]) as mock_post:
            service.create_cards_batch(items)

        payload = mock_post.call_args_list[0][1]["json"]
        assert payload["action"] == "canAddNotesWithErrorDetail"
        probe_notes = payload["params"]["notes"]
        assert len(probe_notes) == 1
        assert len(probe_notes[0]["fields"]) == 1
        assert probe_notes[0]["options"]["allowDuplicate"] is False

    def test_probe_preserves_duplicate_scope_for_deck_builder(self, test_config, make_tokenized_word):
        """allow_duplicate_cards=True: probe keeps duplicateScope, forces allowDuplicate off."""
        import dataclasses

        config = dataclasses.replace(test_config, allow_duplicate_cards=True)
        service = AnkiService(config)
        items = self._make_word_data(make_tokenized_word, n=1)

        probe = _mock_response(result=[{"canAdd": True, "error": None}])
        add = _mock_response(result=[100])

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=[probe, add]) as mock_post:
            result = service.create_cards_batch(items)

        assert len(result) == 1
        probe_notes = mock_post.call_args_list[0][1]["json"]["params"]["notes"]
        assert probe_notes[0]["options"] == {"allowDuplicate": False, "duplicateScope": "deck"}

    def test_non_duplicate_rejection_raises_not_skipped(self, test_config, make_tokenized_word):
        """A non-duplicate canAdd=false surfaces as an error, not a silent skip.

        This is the core fix: the old null-slot inference mislabeled genuine
        rejections (empty first field, bad field mapping) as duplicates.
        """
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=1)

        probe = _mock_response(result=[{"canAdd": False, "error": "cannot create note because it is empty"}])

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=probe),
            pytest.raises(AnkiConnectionError, match="not a duplicate"),
        ):
            service.create_cards_batch(items)

        # It was NOT miscounted as a skipped duplicate.
        assert service.last_skipped_duplicates == 0

    def test_fallback_diffs_two_can_add_notes_calls(self, test_config, make_tokenized_word):
        """Older AnkiConnect (no canAddNotesWithErrorDetail): diff two canAddNotes."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=2)

        unsupported = _mock_response(error="unsupported action")
        # duplicates-allowed arm: both addable; no-duplicates arm: note1 not addable.
        with_dup = _mock_response(result=[True, True])
        without_dup = _mock_response(result=[True, False])
        add = _mock_response(result=[100])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[unsupported, with_dup, without_dup, add],
        ) as mock_post:
            result = service.create_cards_batch(items)

        assert len(result) == 1
        assert service.last_skipped_duplicates == 1  # note1 is the duplicate
        assert self._actions(mock_post) == [
            "canAddNotesWithErrorDetail",
            "canAddNotes",
            "canAddNotes",
            "addNotes",
        ]
        # The duplicates-allowed arm forces allowDuplicate=True so the diff is meaningful.
        dup_allowed_notes = mock_post.call_args_list[1][1]["json"]["params"]["notes"]
        assert dup_allowed_notes[0]["options"]["allowDuplicate"] is True

    def test_probe_transport_error_propagates(self, test_config, make_tokenized_word):
        """A connection failure during the probe aborts the run (not a fallback)."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=1)

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ConnectionError("down"),
            ),
            pytest.raises(AnkiConnectionError, match="Cannot connect"),
        ):
            service.create_cards_batch(items)

    def test_probe_malformed_response_propagates(self, test_config, make_tokenized_word):
        """A non-list probe response is not an 'unsupported action' fallback trigger."""
        service = AnkiService(test_config)
        items = self._make_word_data(make_tokenized_word, n=1)

        bad = _mock_response(result=None)  # not a list

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=bad),
            pytest.raises(AnkiConnectionError, match="canAddNotesWithErrorDetail"),
        ):
            service.create_cards_batch(items)


class TestProbeRetry:
    """In-place bounded retry of the read-only duplicate probes.

    A busy Anki (sync, dialog, database check) accepts the connection but lets
    the probe time out; the probe writes nothing, so replaying it is safe even
    after an earlier chunk's addNotes confirmed a write — the window where the
    queue-level whole-item retry (D30-B) is locked out. addNotes itself must
    never be replayed.
    """

    pytestmark = pytest.mark.real_probe

    _NOTE = {"fields": {"Expression": "食べる"}}

    @pytest.fixture(autouse=True)
    def _no_retry_delay(self, monkeypatch):
        monkeypatch.setattr("anki_miner.services.anki_service._PROBE_RETRY_DELAY_S", 0.0)

    def test_transient_timeout_retried_then_succeeds(self, test_config):
        service = AnkiService(test_config)
        ok = _mock_response(result=[{"canAdd": True, "error": None}])

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[requests.exceptions.ReadTimeout("busy"), ok],
        ) as mock_post:
            assert service._probe_duplicates([dict(self._NOTE)]) == [False]

        assert mock_post.call_count == 2

    def test_gives_up_after_three_attempts(self, test_config):
        service = AnkiService(test_config)

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ReadTimeout("busy"),
            ) as mock_post,
            pytest.raises(AnkiConnectionError, match="timed out after 60s"),
        ):
            service._probe_duplicates([dict(self._NOTE)])

        assert mock_post.call_count == 3

    def test_cancel_stops_retry_after_first_attempt(self, test_config):
        service = AnkiService(test_config)
        service.set_cancelled_check(lambda: True)

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ReadTimeout("busy"),
            ) as mock_post,
            pytest.raises(AnkiConnectionError, match="timed out"),
        ):
            service._probe_duplicates([dict(self._NOTE)])

        assert mock_post.call_count == 1

    def test_add_notes_timeout_never_replayed(self, test_config, make_tokenized_word):
        """The write is not retried: a timeout during addNotes propagates once."""
        service = AnkiService(test_config)
        word = make_tokenized_word(lemma="word_0")
        items = [CardPayload(word=word, media=MediaData(), definition="def")]
        probe = _mock_response(result=[{"canAdd": True, "error": None}])

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=[probe, requests.exceptions.ReadTimeout("busy")],
            ) as mock_post,
            pytest.raises(AnkiConnectionError, match="'addNotes' timed out"),
        ):
            service.create_cards_batch(items)

        assert [c[1]["json"]["action"] for c in mock_post.call_args_list] == [
            "canAddNotesWithErrorDetail",
            "addNotes",
        ]
        # D30: an escaped write attempt leaves uncertainty behind — exactly what
        # blocks the queue-level whole-item replay for this failure.
        assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_UNCERTAIN


class TestNoteFieldPrimitives:
    """find_notes / notes_info / update_notes_fields (used by the Restyle tool)."""

    def test_find_notes(self, test_config):
        service = AnkiService(test_config)
        with patch("anki_miner.services.anki_service.post_action", return_value=[1, 2, 3]) as pa:
            assert service.find_notes("deck:X") == [1, 2, 3]
        assert pa.call_args[0][1] == "findNotes"
        assert pa.call_args[1]["params"] == {"query": "deck:X"}

    def test_notes_info(self, test_config):
        service = AnkiService(test_config)
        info = [{"noteId": 1, "fields": {"Glossary": {"value": "x"}}}]
        with patch("anki_miner.services.anki_service.post_action", return_value=info) as pa:
            assert service.notes_info([1]) == info
        assert pa.call_args[0][1] == "notesInfo"
        assert pa.call_args[1]["params"] == {"notes": [1]}

    def test_notes_info_empty_short_circuits(self, test_config):
        service = AnkiService(test_config)
        with patch("anki_miner.services.anki_service.post_action") as pa:
            assert service.notes_info([]) == []
        pa.assert_not_called()

    def test_update_notes_fields_batches_and_shapes(self, test_config):
        service = AnkiService(test_config)
        updates = [(1, {"Glossary": "a"}), (2, {"Glossary": "b"})]
        with patch("anki_miner.services.anki_service.post_multi", return_value=[None, None]) as pm:
            assert service.update_notes_fields(updates) == [1, 2]
        actions = pm.call_args[0][1]
        assert [a["action"] for a in actions] == ["updateNoteFields", "updateNoteFields"]
        assert actions[0]["params"]["note"] == {"id": 1, "fields": {"Glossary": "a"}}

    def test_update_notes_fields_subtracts_errors(self, test_config):
        service = AnkiService(test_config)
        updates = [(1, {"Glossary": "a"}), (2, {"Glossary": "b"})]
        with patch("anki_miner.services.anki_service.post_multi", return_value=[None, {"error": "boom"}]):
            assert service.update_notes_fields(updates) == [1]

    def test_update_notes_fields_empty_short_circuits(self, test_config):
        service = AnkiService(test_config)
        with patch("anki_miner.services.anki_service.post_multi") as pm:
            assert service.update_notes_fields([]) == []
        pm.assert_not_called()

    def test_update_notes_fields_splits_by_byte_budget(self, test_config, monkeypatch):
        """Large-field notes are split into multiple POSTs by byte budget, not one."""
        service = AnkiService(test_config)
        # Tiny budget so two small notes exceed it and flush into separate chunks.
        monkeypatch.setattr("anki_miner.services.anki_service._UPDATE_NOTES_MAX_BYTES", 10)
        updates = [(1, {"Glossary": "aaaaaaaa"}), (2, {"Glossary": "bbbbbbbb"})]
        with patch("anki_miner.services.anki_service.post_multi", return_value=[None]) as pm:
            assert service.update_notes_fields(updates) == [1, 2]
        # Two separate multi POSTs (one note each), not a single combined body.
        assert pm.call_count == 2
        assert [len(call[0][1]) for call in pm.call_args_list] == [1, 1]

    def test_update_notes_fields_falls_back_to_per_note_on_connection_error(self, test_config):
        """A simulated connection error on a chunk falls back to per-note updates."""
        service = AnkiService(test_config)
        updates = [(1, {"Glossary": "a"}), (2, {"Glossary": "b"})]
        with (
            patch(
                "anki_miner.services.anki_service.post_multi",
                side_effect=AnkiConnectionError("connection reset"),
            ),
            patch("anki_miner.services.anki_service.post_action", return_value=None) as pa,
        ):
            assert service.update_notes_fields(updates) == [1, 2]
        # One tiny per-note POST per note in the failed chunk.
        assert pa.call_count == 2
        assert all(call[0][1] == "updateNoteFields" for call in pa.call_args_list)


class TestAddTags:
    """add_tags (AnkiConnect ``addTags``, used by the Card Backfill tool)."""

    def test_add_tags_payload_shape(self, test_config):
        service = AnkiService(test_config)
        with patch("anki_miner.services.anki_service.post_action", return_value=None) as pa:
            service.add_tags([1, 2], "anki-miner::backfill")
        assert pa.call_count == 1
        assert pa.call_args[0][1] == "addTags"
        assert pa.call_args[1]["params"] == {"notes": [1, 2], "tags": "anki-miner::backfill"}

    def test_add_tags_chunks_at_500(self, test_config):
        service = AnkiService(test_config)
        ids = list(range(1001))
        with patch("anki_miner.services.anki_service.post_action", return_value=None) as pa:
            service.add_tags(ids, "t")
        assert pa.call_count == 3
        sizes = [len(call[1]["params"]["notes"]) for call in pa.call_args_list]
        assert sizes == [500, 500, 1]

    def test_add_tags_empty_short_circuits(self, test_config):
        service = AnkiService(test_config)
        with patch("anki_miner.services.anki_service.post_action") as pa:
            service.add_tags([], "t")
        pa.assert_not_called()

    def test_add_tags_propagates_connection_error(self, test_config):
        service = AnkiService(test_config)
        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=AnkiConnectionError("down"),
            ),
            pytest.raises(AnkiConnectionError),
        ):
            service.add_tags([1], "t")


class TestAnkiWriteState:
    """Note-write provenance recorded around the ``addNotes`` request (D30).

    ``anki_write_state`` answers one question for the retry policy: can this
    process prove no note reached the collection? Everything else — a dropped
    connection mid-request, a response that would not parse — must resolve to
    NOTE_WRITE_UNCERTAIN, because replaying such a run can duplicate cards.
    """

    def _items(self, make_tokenized_word, n=1):
        return [
            CardPayload(word=make_tokenized_word(lemma=f"w_{i}"), media=MediaData(), definition=f"d_{i}")
            for i in range(n)
        ]

    def test_fresh_service_has_written_nothing(self, test_config):
        assert AnkiService(test_config).anki_write_state is AnkiWriteState.NO_NOTE_WRITE

    def test_failure_before_addnotes_keeps_no_note_write(self, test_config, make_tokenized_word):
        """The duplicate probe dies before submission — nothing was in flight."""
        service = AnkiService(test_config)
        items = self._items(make_tokenized_word, n=2)

        with (
            patch.object(AnkiService, "_probe_duplicates", side_effect=AnkiConnectionError("down")),
            pytest.raises(AnkiConnectionError),
        ):
            service.create_cards_batch(items)

        assert service.anki_write_state is AnkiWriteState.NO_NOTE_WRITE

    def test_connection_drop_during_addnotes_is_uncertain(self, test_config, make_tokenized_word):
        """A lost response is indistinguishable from a successful write."""
        service = AnkiService(test_config)
        items = self._items(make_tokenized_word, n=2)

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ConnectionError("reset by peer"),
            ),
            pytest.raises(AnkiConnectionError),
        ):
            service.create_cards_batch(items)

        assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_UNCERTAIN

    def test_malformed_addnotes_response_is_uncertain(self, test_config, make_tokenized_word):
        """The request reached Anki; we just cannot read the answer."""
        service = AnkiService(test_config)
        items = self._items(make_tokenized_word, n=3)

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=_mock_response(result=[100, 101])),
            pytest.raises(AnkiConnectionError),
        ):
            service.create_cards_batch(items)

        assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_UNCERTAIN

    def test_created_note_confirms_the_write(self, test_config, make_tokenized_word):
        service = AnkiService(test_config)
        items = self._items(make_tokenized_word, n=2)

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=_mock_response(result=[100, 101])):
            service.create_cards_batch(items)

        assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_CONFIRMED

    def test_all_null_response_proves_nothing_was_written(self, test_config, make_tokenized_word):
        """A validated all-duplicate response restores the pre-request state."""
        service = AnkiService(test_config)
        items = self._items(make_tokenized_word, n=2)

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=_mock_response(result=[None, None])):
            service.create_cards_batch(items)

        assert service.anki_write_state is AnkiWriteState.NO_NOTE_WRITE

    def test_confirmed_batch_then_failing_batch_is_uncertain(self, test_config, make_tokenized_word):
        """Batch 1 created notes; batch 2's outcome is unknown — the run is unsafe
        to replay, and batch 1's ids stay recorded for Undo."""
        service = AnkiService(test_config)
        items = self._items(make_tokenized_word, n=150)  # 100 + 50

        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=[
                    _mock_response(result=list(range(100))),
                    requests.exceptions.ConnectionError("reset by peer"),
                ],
            ),
            pytest.raises(AnkiConnectionError),
        ):
            service.create_cards_batch(items)

        assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_UNCERTAIN
        assert service.last_created_note_ids == list(range(100))

    def test_all_null_second_batch_keeps_the_earlier_confirmation(self, test_config, make_tokenized_word):
        """Restoring the pre-request state must not downgrade a real confirmation."""
        service = AnkiService(test_config)
        items = self._items(make_tokenized_word, n=150)

        with patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=[
                _mock_response(result=list(range(100))),
                _mock_response(result=[None] * 50),
            ],
        ):
            service.create_cards_batch(items)

        assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_CONFIRMED

    def test_empty_batch_is_not_a_reset_point(self, test_config, make_tokenized_word):
        """Only a new pipeline run clears the state; a no-op call must not
        erase a confirmation the same run earned."""
        service = AnkiService(test_config)

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=_mock_response(result=[100])):
            service.create_cards_batch(self._items(make_tokenized_word, n=1))
        service.create_cards_batch([])

        assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_CONFIRMED


class TestTransientTransportClassification:
    """``is_transient_anki_transport_error`` — source-proven causes only."""

    def _raised(self, cause: Exception) -> AnkiConnectionError:
        try:
            try:
                raise cause
            except Exception as e:
                raise AnkiConnectionError("boom") from e
        except AnkiConnectionError as exc:
            return exc

    @pytest.mark.parametrize(
        "cause",
        [
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.ConnectTimeout("slow"),
            requests.exceptions.ReadTimeout("slow"),
        ],
    )
    def test_connection_and_timeout_causes_are_transient(self, cause):
        assert is_transient_anki_transport_error(self._raised(cause)) is True

    @pytest.mark.parametrize(
        "cause",
        [
            requests.HTTPError("503 Service Unavailable"),
            ValueError("not json"),
        ],
    )
    def test_other_causes_are_not_transient(self, cause):
        """Fail closed: only a proven connection/timeout cause earns a retry."""
        assert is_transient_anki_transport_error(self._raised(cause)) is False

    def test_ankiconnect_side_error_has_no_cause_and_is_not_transient(self):
        assert is_transient_anki_transport_error(AnkiConnectionError("deck was not found")) is False

    def test_unrelated_exception_is_not_transient(self):
        assert is_transient_anki_transport_error(RuntimeError("nope")) is False


class TestGetVocabularyExcludingDeck:
    """get_vocabulary_excluding_deck negates the source deck and skips the cache."""

    def test_query_appends_negated_deck(self, test_config):
        """The findNotes query is the vocab query plus a negated, escaped deck."""
        from dataclasses import replace

        service = AnkiService(replace(test_config, excluded_decks=("RTK",)))

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[1], [{"fields": {"word": {"value": "食べる"}}}]],
        ) as mock_pa:
            result = service.get_vocabulary_excluding_deck("Core_2k")

        assert result == {"食べる"}
        first_call = mock_pa.call_args_list[0]
        assert first_call.args[1] == "findNotes"
        assert first_call.kwargs["params"] == {"query": 'deck:* -deck:"RTK" -deck:"Core\\_2k"'}

    def test_does_not_read_or_write_vocab_cache(self, test_config):
        """The session vocab cache is for _build_vocab_query's shape only."""
        service = AnkiService(test_config)
        service._existing_vocab_cache = {"cached"}

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[1], [{"fields": {"word": {"value": "違う"}}}]],
        ):
            result = service.get_vocabulary_excluding_deck("Premade")

        assert result == {"違う"}
        assert service._existing_vocab_cache == {"cached"}

    def test_transport_failure_raises_never_degrades(self, test_config):
        """An empty degraded set would wrongly keep the whole source deck."""
        service = AnkiService(test_config)

        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=AnkiConnectionError("boom"),
            ),
            pytest.raises(AnkiConnectionError),
        ):
            service.get_vocabulary_excluding_deck("Premade")

    def test_empty_deck_query_returns_empty_set(self, test_config):
        service = AnkiService(test_config)

        with patch("anki_miner.services.anki_service.post_action", side_effect=[[]]):
            assert service.get_vocabulary_excluding_deck("Premade") == set()


class TestAddNotesRaw:
    """add_notes_raw posts caller-built notes with the write-provenance dance."""

    @staticmethod
    def _note(word):
        return {
            "deckName": "Filtered",
            "modelName": "Basic",
            "fields": {"Front": word},
            "tags": ["anki-miner::deckfilter"],
            "options": {"allowDuplicate": True, "duplicateScope": "deck"},
        }

    def test_posts_notes_verbatim_and_returns_aligned_ids(self, test_config):
        service = AnkiService(test_config)
        notes = [self._note("一"), self._note("二")]

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[101, None]],
        ) as mock_pa:
            result = service.add_notes_raw(notes)

        assert result == [101, None]
        call = mock_pa.call_args_list[0]
        assert call.args[1] == "addNotes"
        assert call.kwargs["params"] == {"notes": notes}
        assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_CONFIRMED

    def test_chunks_at_100(self, test_config):
        service = AnkiService(test_config)
        notes = [self._note(str(i)) for i in range(150)]

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[1] * 100, [2] * 50],
        ) as mock_pa:
            result = service.add_notes_raw(notes)

        assert len(result) == 150
        assert len(mock_pa.call_args_list) == 2
        assert len(mock_pa.call_args_list[0].kwargs["params"]["notes"]) == 100
        assert len(mock_pa.call_args_list[1].kwargs["params"]["notes"]) == 50

    def test_all_null_restores_prior_state(self, test_config):
        """An all-duplicate chunk must not erase NO_NOTE_WRITE."""
        service = AnkiService(test_config)
        assert service.anki_write_state is AnkiWriteState.NO_NOTE_WRITE

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[None, None]],
        ):
            result = service.add_notes_raw([self._note("一"), self._note("二")])

        assert result == [None, None]
        assert service.anki_write_state is AnkiWriteState.NO_NOTE_WRITE

    def test_all_null_does_not_downgrade_confirmed(self, test_config):
        """A later all-null chunk cannot erase an earlier confirmed write."""
        service = AnkiService(test_config)

        with patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[7], [None]],
        ):
            service.add_notes_raw([self._note("一")])
            service.add_notes_raw([self._note("二")])

        assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_CONFIRMED

    def test_transport_failure_leaves_uncertain(self, test_config):
        """A request that escapes mid-flight blocks automatic retry."""
        service = AnkiService(test_config)

        with (
            patch(
                "anki_miner.services.anki_service.post_action",
                side_effect=AnkiConnectionError("reset"),
            ),
            pytest.raises(AnkiConnectionError),
        ):
            service.add_notes_raw([self._note("一")])

        assert service.anki_write_state is AnkiWriteState.NOTE_WRITE_UNCERTAIN

    def test_empty_input_no_post(self, test_config):
        service = AnkiService(test_config)

        with patch("anki_miner.services.anki_service.post_action") as mock_pa:
            assert service.add_notes_raw([]) == []

        mock_pa.assert_not_called()
        assert service.anki_write_state is AnkiWriteState.NO_NOTE_WRITE

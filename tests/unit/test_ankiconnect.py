"""Tests for the AnkiConnect HTTP transport: fresh connections and the patch seam.

``post_action``/``post_multi`` send each call on a fresh connection, because
AnkiConnect closes its socket after every response, and many other test modules
patch ``anki_miner.services._ankiconnect.requests.post`` directly (see the module
docstring). These tests pin both halves of that contract.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.exceptions import AnkiBusyError, AnkiConnectionError
from anki_miner.services import _ankiconnect
from anki_miner.services._ankiconnect import post_action, post_multi

_LOGGER = "anki_miner.services._ankiconnect"


def _mock_response(result=None, error=None):
    """Create a mock requests.Response with the given AnkiConnect JSON body."""
    resp = MagicMock()
    resp.json.return_value = {"result": result, "error": error}
    return resp


def _fake_send_into(sent):
    def fake_send(adapter, request, **kwargs):
        sent.append(adapter)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"result": "ok", "error": null}'
        response.request = request
        return response

    return fake_send


class TestFreshConnectionPerCall:
    """AnkiConnect closes its socket after every response, so no call may reuse one."""

    def test_back_to_back_calls_never_share_a_connection_pool(self):
        """BA-019: a pooled keep-alive socket races AnkiConnect's close and resets."""
        sent: list = []
        with patch("requests.adapters.HTTPAdapter.send", autospec=True, side_effect=_fake_send_into(sent)):
            post_action("http://localhost:8765", "modelNames")
            post_action("http://localhost:8765", "modelFieldNames", {"modelName": "Lapis"})

        assert len(sent) == 2
        assert sent[0] is not sent[1]


class TestPatchSeam:
    """The documented ``requests.post`` patch seam still intercepts calls."""

    def test_patching_requests_post_still_intercepts(self):
        resp = _mock_response(result="ok")

        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            result = post_action("http://localhost:8765", "findNotes")

        mock_post.assert_called_once()
        assert result == "ok"


class TestFailureEvidence:
    """Transport failures carry url, timing, and a body snippet."""

    @pytest.fixture(autouse=True)
    def _reset_once_per_process_flags(self):
        _ankiconnect.reset_connection_warning()
        _ankiconnect._ready_logged = False
        yield
        _ankiconnect.reset_connection_warning()
        _ankiconnect._ready_logged = False

    def test_http_500_warning_carries_url_elapsed_and_body_snippet(self, caplog):
        http_response = requests.Response()
        http_response.status_code = 500
        http_response._content = (b"x" * 500) + b"tail"
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error", response=http_response)

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
            pytest.raises(AnkiConnectionError),
        ):
            post_action("http://localhost:8765", "addNotes")

        record = next(r for r in caplog.records if r.getMessage().startswith("AnkiConnect request failed:"))
        message = record.getMessage()
        assert record.levelno == logging.WARNING
        assert "url=http://localhost:8765" in message
        assert "status=500" in message
        assert "elapsed=" in message
        assert "body=" + "x" * 200 in message
        assert "x" * 201 not in message

    def test_connection_refused_warns_once_per_process(self, caplog):
        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ConnectionError("refused"),
            ),
            caplog.at_level(logging.DEBUG, logger=_LOGGER),
        ):
            for _ in range(3):
                with pytest.raises(AnkiConnectionError):
                    post_action("http://localhost:8765", "findNotes")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        debugs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and r.getMessage().startswith("AnkiConnect connection failed:")
        ]
        assert len(debugs) == 3
        assert len(warnings) == 1
        assert "url=http://localhost:8765" in warnings[0].getMessage()

    def test_reset_connection_warning_rearms_the_warning(self, caplog):
        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ConnectionError("refused"),
            ),
            caplog.at_level(logging.DEBUG, logger=_LOGGER),
        ):
            with pytest.raises(AnkiConnectionError):
                post_action("http://localhost:8765", "findNotes")
            _ankiconnect.reset_connection_warning()
            with pytest.raises(AnkiConnectionError):
                post_action("http://localhost:8765", "findNotes")

        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2

    def test_first_success_logs_ankiconnect_ready_once(self, caplog):
        resp = _mock_response(result="ok")

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            caplog.at_level(logging.INFO, logger=_LOGGER),
        ):
            post_action("http://localhost:8765", "findNotes")
            post_action("http://localhost:8765", "findNotes")

        ready = [r for r in caplog.records if r.getMessage().startswith("AnkiConnect ready:")]
        assert len(ready) == 1
        assert ready[0].levelno == logging.INFO
        assert "url=http://localhost:8765" in ready[0].getMessage()
        assert "version=6" in ready[0].getMessage()

    def test_ready_reports_the_servers_own_version_reply(self, caplog):
        resp = _mock_response(result=17)

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            caplog.at_level(logging.INFO, logger=_LOGGER),
        ):
            post_action("http://localhost:8765", "version")

        ready = next(r for r in caplog.records if r.getMessage().startswith("AnkiConnect ready:"))
        assert "version=17" in ready.getMessage()

    def test_post_multi_failure_carries_url_and_elapsed(self, caplog):
        http_response = requests.Response()
        http_response.status_code = 500
        http_response._content = b"boom"
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error", response=http_response)

        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
            pytest.raises(AnkiConnectionError),
        ):
            post_multi("http://localhost:8765", [{"action": "addNote", "version": 6, "params": {}}])

        record = next(r for r in caplog.records if r.getMessage().startswith("AnkiConnect request failed:"))
        message = record.getMessage()
        assert "url=http://localhost:8765" in message
        assert "elapsed=" in message
        assert "body=boom" in message


class TestCollectionBusy:
    """AnkiConnect's busy-collection refusal (a sync running) is AnkiBusyError, not a plain error."""

    _BUSY = "Collection lifecycle operation is active."

    def test_busy_refusal_raises_anki_busy_error_logged_at_debug(self, caplog):
        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=_mock_response(error=self._BUSY)),
            caplog.at_level(logging.DEBUG, logger=_LOGGER),
            pytest.raises(AnkiBusyError, match="busy syncing"),
        ):
            post_action("http://localhost:8765", "modelNames")

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        busy = next(r for r in caplog.records if r.getMessage().startswith("AnkiConnect busy:"))
        assert "action=modelNames" in busy.getMessage() and self._BUSY in busy.getMessage()

    def test_busy_refusal_on_a_multi_envelope_raises_anki_busy_error(self):
        with (
            patch("anki_miner.services._ankiconnect.requests.post", return_value=_mock_response(error=self._BUSY)),
            pytest.raises(AnkiBusyError),
        ):
            post_multi("http://localhost:8765", [{"action": "deckNames", "version": 6, "params": {}}])

    def test_other_errors_stay_plain_anki_connection_errors(self, caplog):
        with (
            patch(
                "anki_miner.services._ankiconnect.requests.post",
                return_value=_mock_response(error="model was not found"),
            ),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
            pytest.raises(AnkiConnectionError) as raised,
        ):
            post_action("http://localhost:8765", "modelFieldNames")

        assert not isinstance(raised.value, AnkiBusyError)
        assert any(r.getMessage().startswith("AnkiConnect error:") for r in caplog.records)

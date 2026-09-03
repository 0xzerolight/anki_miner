"""Tests for the AnkiConnect HTTP transport: keep-alive session and its patch seam.

``post_action``/``post_multi`` reuse one ``requests.Session`` across calls
instead of a fresh connection per call, but many other test modules patch
``anki_miner.services._ankiconnect.requests.post`` directly (see the module
docstring). These tests pin both halves of that contract.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.services import _ankiconnect
from anki_miner.services._ankiconnect import post_action, post_multi

_LOGGER = "anki_miner.services._ankiconnect"


def _mock_response(result=None, error=None):
    """Create a mock requests.Response with the given AnkiConnect JSON body."""
    resp = MagicMock()
    resp.json.return_value = {"result": result, "error": error}
    return resp


@pytest.fixture(autouse=True)
def _reset_shared_session():
    """Isolate the module-level session singleton between tests."""
    previous = _ankiconnect._session
    _ankiconnect._session = None
    yield
    _ankiconnect._session = previous


class TestSharedSession:
    """post_action/post_multi keep one Session alive across calls."""

    def test_two_calls_reuse_one_session(self):
        resp = _mock_response(result="ok")
        session = MagicMock()
        session.post.return_value = resp

        with patch.object(_ankiconnect.requests, "Session", return_value=session) as session_cls:
            post_action("http://localhost:8765", "findNotes")
            post_action("http://localhost:8765", "findNotes")

        session_cls.assert_called_once()
        assert session.post.call_count == 2


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

"""Tests for the AnkiConnect HTTP transport: keep-alive session and its patch seam.

``post_action``/``post_multi`` reuse one ``requests.Session`` across calls
instead of a fresh connection per call, but many other test modules patch
``anki_miner.services._ankiconnect.requests.post`` directly (see the module
docstring). These tests pin both halves of that contract.
"""

from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services import _ankiconnect
from anki_miner.services._ankiconnect import post_action


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

"""AI-02 on the wire: the key goes in every body (multi's sub-actions too) and in no log line."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.services import _ankiconnect
from anki_miner.services._ankiconnect import post_action, post_multi

_LOGGER = "anki_miner.services._ankiconnect"
_POST = "anki_miner.services._ankiconnect.requests.post"
_KEYED = "http://127.0.0.1:8765/?key=SECRET"


def _response(result=None, error=None) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"result": result, "error": error}
    return response


@pytest.fixture(autouse=True)
def _fresh_log_state():
    _ankiconnect.reset_connection_warning()
    _ankiconnect._ready_logged = False
    yield
    _ankiconnect.reset_connection_warning()
    _ankiconnect._ready_logged = False


def test_post_action_sends_the_key_in_the_body_to_the_bare_endpoint():
    with patch(_POST, return_value=_response(result=6)) as post:
        assert post_action(_KEYED, "version") == 6

    assert post.call_args.args[0] == "http://127.0.0.1:8765/"
    assert post.call_args.kwargs["json"] == {"action": "version", "version": 6, "params": {}, "key": "SECRET"}


def test_without_a_key_the_request_is_unchanged():
    with patch(_POST, return_value=_response(result=6)) as post:
        post_action("http://127.0.0.1:8765", "version")

    assert post.call_args.args[0] == "http://127.0.0.1:8765"
    assert post.call_args.kwargs["json"] == {"action": "version", "version": 6, "params": {}}


def test_an_empty_key_sends_no_key():
    with patch(_POST, return_value=_response(result=6)) as post:
        post_action("http://127.0.0.1:8765/?key=", "version")

    assert "key" not in post.call_args.kwargs["json"]


def test_multi_carries_the_key_on_the_envelope_and_every_action():
    actions = [
        {"action": "findNotes", "version": 6, "params": {"query": "deck:*"}},
        {"action": "deckNames", "version": 6, "params": {}},
    ]
    with patch(_POST, return_value=_response(result=[[], []])) as post:
        post_multi(_KEYED, actions)

    body = post.call_args.kwargs["json"]
    assert post.call_args.args[0] == "http://127.0.0.1:8765/"
    assert body["key"] == "SECRET"
    assert [action["key"] for action in body["params"]["actions"]] == ["SECRET", "SECRET"]
    assert "key" not in actions[0]  # the caller's list is not mutated


def test_no_log_line_shows_the_key(caplog):
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        with patch(_POST, return_value=_response(result=6)):
            post_action(_KEYED, "version")
        with (
            patch(_POST, return_value=_response(error="collection is not available")),
            pytest.raises(AnkiConnectionError),
        ):
            post_action(_KEYED, "deckNames")
        with (
            patch(_POST, side_effect=requests.exceptions.ConnectionError("refused")),
            pytest.raises(AnkiConnectionError),
        ):
            post_multi(_KEYED, [{"action": "deckNames", "version": 6, "params": {}}])
        with patch(_POST, side_effect=requests.exceptions.ReadTimeout("slow")), pytest.raises(AnkiConnectionError):
            post_action(_KEYED, "findNotes")

    messages = [record.getMessage() for record in caplog.records]
    assert messages
    assert not [m for m in messages if "SECRET" in m]
    assert any("key=***" in m for m in messages)

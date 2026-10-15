"""AI-02 (ruling R7): the API key rides in the AnkiConnect URL; split it off, never log it."""

from __future__ import annotations

import pytest

from anki_miner.services._ankiconnect import redact_ankiconnect_url, split_ankiconnect_url


@pytest.mark.parametrize(
    ("url", "endpoint", "key"),
    [
        ("http://127.0.0.1:8765", "http://127.0.0.1:8765", None),
        ("http://127.0.0.1:8765/?key=SECRET", "http://127.0.0.1:8765/", "SECRET"),
        ("http://127.0.0.1:8765?key=SECRET", "http://127.0.0.1:8765", "SECRET"),
        ("http://lan:8765/?a=1&key=S%2Bx", "http://lan:8765/?a=1", "S+x"),
        ("http://lan:8765/?key=a+b", "http://lan:8765/", "a+b"),
        ("http://lan:8765/?key=", "http://lan:8765/", ""),
        ("http://lan:8765/?keys=1", "http://lan:8765/?keys=1", None),
    ],
)
def test_split(url, endpoint, key):
    assert split_ankiconnect_url(url) == (endpoint, key)


@pytest.mark.parametrize(
    ("url", "logged"),
    [
        ("http://127.0.0.1:8765", "http://127.0.0.1:8765"),
        ("http://127.0.0.1:8765/?key=SECRET", "http://127.0.0.1:8765/?key=***"),
        ("http://lan:8765/?a=1&key=SECRET", "http://lan:8765/?a=1&key=***"),
        ("http://lan:8765/?key=", "http://lan:8765/?key=***"),
    ],
)
def test_redact(url, logged):
    assert redact_ankiconnect_url(url) == logged


def test_a_malformed_url_is_left_alone():
    assert split_ankiconnect_url("http://[::1") == ("http://[::1", None)

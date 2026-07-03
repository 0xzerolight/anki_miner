"""Unit tests for the dictionary update check-and-notify service.

Network is fully mocked via a fake session object — no real socket is opened,
so these tests do not need the ``network`` marker.
"""

from __future__ import annotations

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.updater import (
    UpdateInfo,
    check_for_update,
    compare_revisions,
)


class _FakeResponse:
    def __init__(self, payload: object, *, status_ok: bool = True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self) -> None:
        if not self._status_ok:
            raise RuntimeError("HTTP 500")

    def json(self) -> object:
        return self._payload


class _FakeSession:
    """Records the URL/timeout it was called with and returns a canned response."""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[tuple[str, object]] = []

    def get(self, url: str, timeout: object = None) -> _FakeResponse:
        self.calls.append((url, timeout))
        return self._response


# --- compare_revisions (verbatim port table) --------------------------------


@pytest.mark.parametrize(
    "current,latest,expected",
    [
        # Numeric dot-separated, same arity: part-wise integer compare.
        ("1.0", "1.1", True),
        ("1.1", "1.0", False),
        ("1.0", "1.0", False),
        ("2.0", "10.0", True),  # integer, not lexical (2 < 10)
        ("10.0", "2.0", False),
        ("24.1.1.1", "24.1.1.2", True),
        ("24.1.1.2", "24.1.1.1", False),
        # Differing arity falls back to string compare.
        ("1.0", "1.0.0", True),  # "1.0" < "1.0.0"
        ("1.0.0", "1.0", False),
        # Non-simple revisions fall back to string compare.
        ("1.0.0-alpha", "1.0.0-beta", True),
        ("2024-01-01", "2024-02-01", True),
        ("2024-02-01", "2024-01-01", False),
        ("abc", "abc", False),
        # A numeric current vs a non-numeric latest → string fallback.
        ("1.0", "1.0a", True),
    ],
)
def test_compare_revisions_table(current: str, latest: str, expected: bool) -> None:
    assert compare_revisions(current, latest) is expected


# --- check_for_update -------------------------------------------------------


def _updatable_meta(**overrides: str) -> dict[str, str]:
    meta = {
        "is_updatable": "true",
        "index_url": "https://example.com/index.json",
        "download_url": "https://example.com/dict-v1.zip",
        "source_revision": "1.0",
    }
    meta.update(overrides)
    return meta


def test_returns_update_info_when_remote_newer() -> None:
    session = _FakeSession(
        _FakeResponse(
            {
                "title": "Jitendex",
                "revision": "2.0",
                "format": 3,
                "downloadUrl": "https://example.com/dict-v2.zip",
            }
        )
    )
    info = check_for_update(_updatable_meta(), session=session, timeout=7.0)
    assert info == UpdateInfo(
        current_revision="1.0",
        latest_revision="2.0",
        download_url="https://example.com/dict-v2.zip",
    )
    # The stored index_url was fetched with the given timeout.
    assert session.calls == [("https://example.com/index.json", 7.0)]


def test_returns_none_when_remote_same_or_older() -> None:
    session = _FakeSession(_FakeResponse({"title": "D", "revision": "1.0", "format": 3}))
    assert check_for_update(_updatable_meta(), session=session) is None


def test_falls_back_to_current_download_url_when_remote_lacks_one() -> None:
    session = _FakeSession(_FakeResponse({"title": "D", "revision": "2.0", "format": 3}))
    info = check_for_update(_updatable_meta(), session=session)
    assert info is not None
    assert info.download_url == "https://example.com/dict-v1.zip"


def test_ignores_non_http_remote_download_url() -> None:
    session = _FakeSession(
        _FakeResponse(
            {
                "title": "D",
                "revision": "2.0",
                "format": 3,
                "downloadUrl": "file:///etc/passwd",
            }
        )
    )
    info = check_for_update(_updatable_meta(), session=session)
    assert info is not None
    # A non-http(s) remote URL is rejected; the trusted stored URL is kept.
    assert info.download_url == "https://example.com/dict-v1.zip"


def test_returns_none_when_not_marked_updatable() -> None:
    session = _FakeSession(_FakeResponse({"title": "D", "revision": "9.0", "format": 3}))
    meta = _updatable_meta()
    del meta["is_updatable"]
    assert check_for_update(meta, session=session) is None
    # No network work is done for a non-updatable dict.
    assert session.calls == []


def test_returns_none_when_index_url_missing() -> None:
    session = _FakeSession(_FakeResponse({"title": "D", "revision": "9.0", "format": 3}))
    meta = _updatable_meta()
    del meta["index_url"]
    assert check_for_update(meta, session=session) is None
    assert session.calls == []


def test_raises_on_structurally_invalid_remote_index() -> None:
    session = _FakeSession(_FakeResponse({"revision": "2.0"}))  # missing title
    with pytest.raises(SetupError):
        check_for_update(_updatable_meta(), session=session)


def test_raises_on_non_object_remote_index() -> None:
    session = _FakeSession(_FakeResponse(["not", "an", "object"]))
    with pytest.raises(SetupError):
        check_for_update(_updatable_meta(), session=session)

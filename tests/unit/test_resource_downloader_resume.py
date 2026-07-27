"""The downloader's resume leg (D16-C) — and every reason it refuses to use it.

A partial transfer surviving a restart is only worth having if it can never
splice bytes from two different builds of an artifact. ``TestResumeRejection``
is that guarantee: each case proves the app threw away a usable-looking prefix
and fetched from byte zero rather than hand back a file that passes a length
check and fails everything after it.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import pytest
import requests

from anki_miner.exceptions import SetupError
from anki_miner.services import resource_downloader
from anki_miner.services.download_resume import ResumeManifest, ResumeState
from anki_miner.services.resource_downloader import download_to_temp
from tests.unit.test_resource_downloader import URL, _response

_ETAG = '"v1"'
_BODY = b"0123456789abcdefghij"  # 20 bytes


def _full_response(body=_BODY, *, etag=_ETAG, last_modified=None, extra=None):
    """A plain 200 carrying the whole representation."""
    headers = {"Content-Length": str(len(body))}
    if etag:
        headers["ETag"] = etag
    if last_modified:
        headers["Last-Modified"] = last_modified
    headers.update(extra or {})
    return _response(content=body, headers=headers)


def _ranged_response(body, *, start, total, etag=_ETAG, extra=None, status=206):
    """A 206 continuing at ``start`` of ``total``."""
    headers = {
        "Content-Length": str(len(body)),
        "Content-Range": f"bytes {start}-{start + len(body) - 1}/{total}",
    }
    if etag:
        headers["ETag"] = etag
    headers.update(extra or {})
    return _response(content=body, status_code=status, headers=headers)


def _server_error(status=503):
    """A 5xx whose ``HTTPError`` carries its response, as requests' does.

    The bare ``HTTPError`` the shared helper raises has ``response is None``,
    which the retry predicate reads as permanent — fine for the tests that use
    it, wrong for proving a transient server fault keeps the partial.
    """
    resp = _response(content=b"", status_code=status, headers={})
    error = requests.HTTPError(str(status))
    error.response = resp
    resp.raise_for_status.side_effect = error
    return resp


class _Server:
    """Hands out queued responses and records the request headers it saw."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, url, **kwargs):
        self.requests.append(dict(kwargs.get("headers") or {}))
        return self._responses.pop(0)


def _write_manifest(state, *, length, total, etag=_ETAG):
    payload = state.part_path.read_bytes()[:length]
    manifest = ResumeManifest(
        url=URL,
        total=total,
        length=length,
        sha256=hashlib.sha256(payload).hexdigest(),
        etag=etag,
        last_modified=None,
    )
    state.manifest_path.write_text(json.dumps(manifest.to_json()), encoding="utf-8")


def _seeded(resume_root, *, length=10, total=20, etag=_ETAG):
    """A durable 10-of-20-byte partial, as a previous run would have left it."""
    state = ResumeState(resume_root, "res")
    state.ensure_root()
    state.part_path.write_bytes(_BODY[:length])
    _write_manifest(state, length=length, total=total, etag=etag)
    return state


class TestResumeAcrossRestarts:
    """The 580-of-600 MB case: the partial is kept, then continued."""

    def test_a_cancelled_transfer_keeps_its_bytes_and_a_manifest(self, tmp_path):
        resume_root = tmp_path / "resume"
        stop = {"remaining": 2}

        def cancelled():
            if stop["remaining"] <= 0:
                return True
            stop["remaining"] -= 1
            return False

        response = _full_response()
        response.iter_content.side_effect = lambda chunk_size=8192: iter([b"01234", b"56789", b"abcde"])
        with patch("requests.Session.get", return_value=response), pytest.raises(SetupError, match="cancelled"):
            download_to_temp(
                URL,
                dest_dir=tmp_path,
                cancelled_check=cancelled,
                resume_key="res",
                resume_root=resume_root,
            )

        state = ResumeState(resume_root, "res")
        assert state.part_path.exists()
        manifest = state.load()
        assert manifest is not None
        assert manifest.length == state.part_path.stat().st_size > 0

    def test_the_next_call_asks_only_for_the_missing_bytes(self, tmp_path):
        resume_root = tmp_path / "resume"
        _seeded(resume_root)
        server = _Server(_ranged_response(_BODY[10:], start=10, total=20))

        with patch("requests.Session.get", side_effect=server):
            result = download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)

        assert server.requests[0]["Range"] == "bytes=10-"
        assert server.requests[0]["If-Range"] == _ETAG
        assert server.requests[0]["Accept-Encoding"] == "identity"
        assert result.read_bytes() == _BODY

    def test_a_completed_resume_leaves_no_state_behind(self, tmp_path):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        with patch("requests.Session.get", side_effect=_Server(_ranged_response(_BODY[10:], start=10, total=20))):
            download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        assert not state.part_path.exists()
        assert not state.manifest_path.exists()

    def test_the_size_cap_counts_the_whole_artifact_not_just_the_new_bytes(self, tmp_path):
        resume_root = tmp_path / "resume"
        _seeded(resume_root)
        with (
            patch("requests.Session.get", side_effect=_Server(_ranged_response(_BODY[10:], start=10, total=20))),
            pytest.raises(SetupError, match="size cap"),
        ):
            download_to_temp(URL, dest_dir=tmp_path, max_bytes=15, resume_key="res", resume_root=resume_root)

    def test_a_server_offering_no_validator_keeps_nothing(self, tmp_path):
        """Nothing a later resume could prove — so no partial is left to offer."""
        resume_root = tmp_path / "resume"
        response = _full_response(etag=None)
        response.iter_content.side_effect = requests.ConnectionError("dropped")

        with (
            patch("requests.Session.get", return_value=response),
            patch.object(resource_downloader.time, "sleep"),
            pytest.raises(SetupError),
        ):
            download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)

        assert not ResumeState(resume_root, "res").part_path.exists()
        assert not list(tmp_path.glob("*.part"))

    def test_without_a_resume_key_nothing_is_persisted_at_all(self, tmp_path):
        resume_root = tmp_path / "resume"
        with patch("requests.Session.get", return_value=_full_response()):
            download_to_temp(URL, dest_dir=tmp_path)
        assert not resume_root.exists()


class TestResumeRejection:
    """Every case here restarts clean rather than splicing two artifacts."""

    def _assert_restarted_clean(self, server, result, state):
        # Two requests: the rejected ranged one, then a plain full fetch.
        assert len(server.requests) == 2
        assert "Range" not in server.requests[1]
        assert result.read_bytes() == _BODY
        assert not state.manifest_path.exists()

    def test_a_200_is_never_appended_to(self, tmp_path):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        # A server that ignores If-Range answers 200 with the WHOLE body.
        server = _Server(_full_response(), _full_response())
        with patch("requests.Session.get", side_effect=server):
            result = download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        self._assert_restarted_clean(server, result, state)

    @pytest.mark.parametrize("status", [412, 416])
    def test_a_precondition_or_range_refusal_restarts_clean(self, tmp_path, status):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        server = _Server(_response(content=b"", status_code=status, headers={}), _full_response())
        with patch("requests.Session.get", side_effect=server):
            result = download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        self._assert_restarted_clean(server, result, state)

    def test_a_range_starting_somewhere_else_restarts_clean(self, tmp_path):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        server = _Server(_ranged_response(_BODY[5:], start=5, total=20), _full_response())
        with patch("requests.Session.get", side_effect=server):
            result = download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        self._assert_restarted_clean(server, result, state)

    def test_a_changed_total_restarts_clean(self, tmp_path):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        server = _Server(_ranged_response(_BODY[10:], start=10, total=999), _full_response())
        with patch("requests.Session.get", side_effect=server):
            result = download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        self._assert_restarted_clean(server, result, state)

    def test_a_changed_etag_restarts_clean(self, tmp_path):
        """The corruption case: a newer build served against an older prefix."""
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        server = _Server(_ranged_response(_BODY[10:], start=10, total=20, etag='"v2"'), _full_response())
        with patch("requests.Session.get", side_effect=server):
            result = download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        self._assert_restarted_clean(server, result, state)

    def test_a_weak_validator_on_the_stored_state_is_no_state_at_all(self, tmp_path):
        resume_root = tmp_path / "resume"
        _seeded(resume_root, etag='W/"v1"')
        server = _Server(_full_response())
        with patch("requests.Session.get", side_effect=server):
            result = download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        # The stored state was worthless, so ONE request went out with no Range.
        assert len(server.requests) == 1
        assert "Range" not in server.requests[0]
        assert result.read_bytes() == _BODY

    def test_an_encoded_ranged_body_restarts_clean(self, tmp_path):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        encoded = _ranged_response(_BODY[10:], start=10, total=20, extra={"Content-Encoding": "gzip"})
        server = _Server(encoded, _full_response())
        with patch("requests.Session.get", side_effect=server):
            result = download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        self._assert_restarted_clean(server, result, state)

    def test_an_unparseable_content_range_restarts_clean(self, tmp_path):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        broken = _response(content=_BODY[10:], status_code=206, headers={"Content-Range": "bytes */20"})
        server = _Server(broken, _full_response())
        with patch("requests.Session.get", side_effect=server):
            result = download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        self._assert_restarted_clean(server, result, state)

    def test_a_corrupted_prefix_restarts_clean_without_asking_for_a_range(self, tmp_path):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        state.part_path.write_bytes(b"XXXXXXXXXX")
        server = _Server(_full_response())
        with patch("requests.Session.get", side_effect=server):
            result = download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        assert len(server.requests) == 1
        assert "Range" not in server.requests[0]
        assert result.read_bytes() == _BODY


class TestPartialRetention:
    """Which failures keep the bytes, and which throw them away."""

    def test_a_5xx_on_the_ranged_leg_keeps_the_partial(self, tmp_path):
        """A bad gateway is not proof the artifact changed."""
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        server = _Server(*[_server_error() for _ in range(3)])
        with (
            patch("requests.Session.get", side_effect=server),
            patch.object(resource_downloader.time, "sleep"),
            pytest.raises(SetupError),
        ):
            download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        # All three attempts asked for the range; none of them threw the bytes away.
        assert [headers.get("Range") for headers in server.requests] == ["bytes=10-"] * 3
        assert state.manifest_path.exists()
        assert state.part_path.read_bytes() == _BODY[:10]

    def test_a_dropped_connection_keeps_the_partial(self, tmp_path):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        responses = []
        for _ in range(3):
            resp = _ranged_response(_BODY[10:], start=10, total=20)
            resp.iter_content.side_effect = requests.ConnectionError("dropped")
            responses.append(resp)
        with (
            patch("requests.Session.get", side_effect=_Server(*responses)),
            patch.object(resource_downloader.time, "sleep"),
            pytest.raises(SetupError),
        ):
            download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        assert state.manifest_path.exists()

    def test_a_permanent_404_discards_the_partial(self, tmp_path):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        server = _Server(_response(content=b"", status_code=404, headers={}))
        with patch("requests.Session.get", side_effect=server), pytest.raises(SetupError):
            download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        assert not state.manifest_path.exists()
        assert not state.part_path.exists()

    def test_a_truncated_resumed_body_discards_the_partial(self, tmp_path):
        resume_root = tmp_path / "resume"
        state = _seeded(resume_root)
        short = _ranged_response(_BODY[10:15], start=10, total=20)
        short.headers["Content-Length"] = "10"
        with (
            patch("requests.Session.get", side_effect=_Server(short)),
            pytest.raises(SetupError, match="truncated"),
        ):
            download_to_temp(URL, dest_dir=tmp_path, resume_key="res", resume_root=resume_root)
        assert not state.manifest_path.exists()
        assert not state.part_path.exists()

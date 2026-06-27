"""Tests for the streaming resource downloader."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.exceptions import SetupError
from anki_miner.services import resource_downloader
from anki_miner.services.resource_downloader import download_to_temp

URL = "https://example.com/resource.zip"


def _response(
    *,
    content: bytes = b"payload-data",
    status_code: int = 200,
    headers: dict | None = None,
    chunks: list[bytes] | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers if headers is not None else {"Content-Length": str(len(content))}
    body_chunks = chunks if chunks is not None else [content]
    resp.iter_content.side_effect = lambda chunk_size=8192: iter(body_chunks)
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _part_files(d):
    return list(d.glob("*.part"))


class TestDownloadToTemp:
    def test_success_writes_part_temp_and_returns_it(self, tmp_path):
        with patch("requests.Session.get", return_value=_response(content=b"abc123")):
            result = download_to_temp(URL, dest_dir=tmp_path)

        assert result.parent == tmp_path
        assert result.suffix == ".part"
        assert result.exists()
        assert result.read_bytes() == b"abc123"

    def test_creates_dest_dir_if_missing(self, tmp_path):
        dest = tmp_path / "nested" / "downloads"
        with patch("requests.Session.get", return_value=_response()):
            result = download_to_temp(URL, dest_dir=dest)
        assert dest.is_dir()
        assert result.parent == dest

    def test_does_not_create_final_destination(self, tmp_path):
        # Only a .part file should appear — no non-.part artifact.
        with patch("requests.Session.get", return_value=_response(content=b"xyz")):
            download_to_temp(URL, dest_dir=tmp_path)
        all_files = list(tmp_path.iterdir())
        assert len(all_files) == 1
        assert all_files[0].suffix == ".part"

    def test_progress_callback_invoked(self, tmp_path):
        calls: list[tuple[int, int, str]] = []
        chunks = [b"aaaa", b"bbbb", b"cccc"]
        headers = {"Content-Length": "12"}
        with patch(
            "requests.Session.get",
            return_value=_response(chunks=chunks, headers=headers),
        ):
            download_to_temp(URL, dest_dir=tmp_path, progress=lambda d, t, m: calls.append((d, t, m)))

        assert calls  # invoked at least once
        # Last call reports the full downloaded size and the total from header.
        assert calls[-1][0] == 12
        assert calls[-1][1] == 12

    def test_progress_total_zero_when_no_content_length(self, tmp_path):
        calls: list[tuple[int, int, str]] = []
        with patch(
            "requests.Session.get",
            return_value=_response(content=b"data", headers={}),
        ):
            download_to_temp(URL, dest_dir=tmp_path, progress=lambda d, t, m: calls.append((d, t, m)))
        assert all(t == 0 for _, t, _ in calls)

    def test_cancellation_before_request_raises_no_files(self, tmp_path):
        get = MagicMock()
        with (
            patch("requests.Session.get", get),
            pytest.raises(SetupError, match="cancelled"),
        ):
            download_to_temp(URL, dest_dir=tmp_path, cancelled_check=lambda: True)
        get.assert_not_called()
        assert _part_files(tmp_path) == []

    def test_cancellation_mid_stream_cleans_up_and_raises(self, tmp_path):
        chunks = [b"aaaa", b"bbbb", b"cccc"]
        # Cancel on the second chunk-iteration check.
        state = {"calls": 0}

        def cancel():
            state["calls"] += 1
            return state["calls"] >= 2

        with (
            patch("requests.Session.get", return_value=_response(chunks=chunks)),
            pytest.raises(SetupError, match="cancelled"),
        ):
            download_to_temp(URL, dest_dir=tmp_path, cancelled_check=cancel)

        assert _part_files(tmp_path) == []

    def test_http_error_raises_setup_error_no_temp(self, tmp_path):
        with (
            patch("requests.Session.get", return_value=_response(status_code=404)),
            pytest.raises(SetupError),
        ):
            download_to_temp(URL, dest_dir=tmp_path)
        assert _part_files(tmp_path) == []

    def test_network_failure_raises_setup_error_no_temp(self, tmp_path):
        with (
            patch("requests.Session.get", side_effect=requests.ConnectionError("boom")),
            pytest.raises(SetupError),
        ):
            download_to_temp(URL, dest_dir=tmp_path)
        assert _part_files(tmp_path) == []

    def test_size_cap_exceeded_raises_and_cleans_up(self, tmp_path):
        # Patch the cap small so we don't allocate hundreds of MB in the test.
        chunks = [b"x" * 100, b"x" * 100]
        with (
            patch.object(resource_downloader, "MAX_DOWNLOAD_BYTES", 150),
            patch("requests.Session.get", return_value=_response(chunks=chunks)),
            pytest.raises(SetupError, match="size cap"),
        ):
            download_to_temp(URL, dest_dir=tmp_path)
        assert _part_files(tmp_path) == []

    def test_size_cap_param_overrides_default(self, tmp_path):
        # A response larger than a small passed max_bytes raises the size-cap
        # error, even though it is well under MAX_DOWNLOAD_BYTES.
        chunks = [b"x" * 100, b"x" * 100]
        with (
            patch("requests.Session.get", return_value=_response(chunks=chunks)),
            pytest.raises(SetupError, match="size cap"),
        ):
            download_to_temp(URL, dest_dir=tmp_path, max_bytes=150)
        assert _part_files(tmp_path) == []

    def test_size_cap_param_default_is_max_download_bytes(self, tmp_path):
        # With the default max_bytes, the cap is MAX_DOWNLOAD_BYTES: patching it
        # small triggers the cap without passing max_bytes explicitly.
        chunks = [b"x" * 100, b"x" * 100]
        with (
            patch.object(resource_downloader, "MAX_DOWNLOAD_BYTES", 150),
            patch("requests.Session.get", return_value=_response(chunks=chunks)),
            pytest.raises(SetupError, match="size cap"),
        ):
            download_to_temp(URL, dest_dir=tmp_path)
        assert _part_files(tmp_path) == []

    def test_larger_max_bytes_allows_download(self, tmp_path):
        # A response that would exceed a small cap is allowed when a larger
        # max_bytes is passed.
        chunks = [b"x" * 100, b"x" * 100]
        resp = _response(chunks=chunks, headers={"Content-Length": "200"})
        with patch("requests.Session.get", return_value=resp):
            result = download_to_temp(URL, dest_dir=tmp_path, max_bytes=10_000)
        assert result.exists()
        assert result.read_bytes() == b"x" * 200

    def test_content_length_mismatch_raises_and_cleans_up(self, tmp_path):
        # Server advertises 100 bytes but the body is short (5). Even if requests
        # didn't raise, the explicit byte-count check must reject the partial.
        resp = _response(content=b"short", headers={"Content-Length": "100"})
        with (
            patch("requests.Session.get", return_value=resp),
            pytest.raises(SetupError, match="truncated"),
        ):
            download_to_temp(URL, dest_dir=tmp_path)
        assert _part_files(tmp_path) == []

    def test_browser_user_agent_set_on_session(self):
        ua = resource_downloader._new_session().headers["User-Agent"]
        assert not ua.lower().startswith("python-requests")

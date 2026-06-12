"""Tests for JPod101AudioFetcher."""

import hashlib
from unittest.mock import MagicMock, patch

import requests

from anki_miner.services.expression_audio_fetcher import (
    JPOD101_NOT_FOUND_SHA256,
    MAX_AUDIO_BYTES,
    JPod101AudioFetcher,
)

MODULE = "anki_miner.services.expression_audio_fetcher"

# Minimal valid ID3v2-tagged MP3 body for tests that expect a successful cache write.
_VALID_MP3 = b"ID3" + b"\x00" * 7 + b"\xff\xfb\x90\x00" + b"\x00" * 100


def _response(
    status_code: int = 200,
    content: bytes = _VALID_MP3,
    url: str = "https://assets.languagepod101.com/dictionary/japanese/audiomp3.php",
) -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.url = url
    # Support streamed reading: iter_content yields the whole body as one chunk.
    mock_response.iter_content.side_effect = lambda chunk_size=8192: iter([content])
    return mock_response


class TestJPod101AudioFetcher:
    """Tests for JPod101AudioFetcher."""

    def test_fetch_success_writes_mp3_and_returns_path(self, tmp_path):
        """Successful fetch downloads, caches, and returns the mp3 path."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.requests.get", return_value=_response()) as mock_get:
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.exists()
        assert result.suffix == ".mp3"
        assert result.read_bytes() == _VALID_MP3
        assert result.parent == tmp_path
        params = mock_get.call_args.kwargs["params"]
        assert params["kanji"] == "食べる"
        assert params["kana"] == "たべる"

    def test_placeholder_hash_returns_none_and_writes_miss_marker(self, tmp_path):
        """Not-found placeholder audio writes a zero-byte .miss marker."""
        placeholder = b"audio-not-available-placeholder"
        digest = hashlib.sha256(placeholder).hexdigest()
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with (
            patch(f"{MODULE}.JPOD101_NOT_FOUND_SHA256", digest),
            patch(f"{MODULE}.requests.get", return_value=_response(content=placeholder)),
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        miss_files = list(tmp_path.glob("*.miss"))
        assert len(miss_files) == 1
        assert miss_files[0].stat().st_size == 0
        assert not list(tmp_path.glob("*.mp3"))

    def test_timeout_returns_none_without_miss_marker(self, tmp_path):
        """Timeout is swallowed and does not poison the cache."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.requests.get", side_effect=requests.exceptions.Timeout):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.miss"))
        assert not list(tmp_path.glob("*.mp3"))

    def test_request_exception_returns_none_without_miss_marker(self, tmp_path):
        """Connection errors are swallowed and do not poison the cache."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.requests.get", side_effect=requests.RequestException):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.miss"))
        assert not list(tmp_path.glob("*.mp3"))

    def test_non_200_returns_none_without_miss_marker(self, tmp_path):
        """Transient server errors must not write a miss marker."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.requests.get", return_value=_response(status_code=503)):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.miss"))
        assert not list(tmp_path.glob("*.mp3"))

    def test_cache_hit_skips_network_and_sleep(self, tmp_path):
        """Existing non-empty mp3 short-circuits without network or delay."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0.2)
        with patch(f"{MODULE}.requests.get", return_value=_response()):
            first = fetcher.fetch("食べる", "たべる")
        assert first is not None

        with (
            patch(f"{MODULE}.requests.get") as mock_get,
            patch(f"{MODULE}.time.sleep") as mock_sleep,
        ):
            second = fetcher.fetch("食べる", "たべる")

        assert second == first
        mock_get.assert_not_called()
        mock_sleep.assert_not_called()

    def test_miss_marker_skips_network(self, tmp_path):
        """Existing .miss marker short-circuits to None without network."""
        placeholder = b"placeholder"
        digest = hashlib.sha256(placeholder).hexdigest()
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with (
            patch(f"{MODULE}.JPOD101_NOT_FOUND_SHA256", digest),
            patch(f"{MODULE}.requests.get", return_value=_response(content=placeholder)),
        ):
            fetcher.fetch("食べる", "たべる")

        with (
            patch(f"{MODULE}.requests.get") as mock_get,
            patch(f"{MODULE}.time.sleep") as mock_sleep,
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        mock_get.assert_not_called()
        mock_sleep.assert_not_called()

    def test_empty_reading_still_fetches(self, tmp_path):
        """Empty reading fetches with kanji only."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.requests.get", return_value=_response()) as mock_get:
            result = fetcher.fetch("食べる", "")

        assert result is not None
        mock_get.assert_called_once()
        params = mock_get.call_args.kwargs["params"]
        assert params["kanji"] == "食べる"

    def test_empty_mined_form_returns_none_without_network(self, tmp_path):
        """Empty or whitespace mined_form short-circuits to None."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.requests.get") as mock_get:
            assert fetcher.fetch("", "たべる") is None
            assert fetcher.fetch("   ", "たべる") is None

        mock_get.assert_not_called()

    def test_delay_applied_before_network_fetch(self, tmp_path):
        """time.sleep is called with the constructor delay before the request."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0.7)
        call_order: list[str] = []
        with (
            patch(
                f"{MODULE}.time.sleep",
                side_effect=lambda _s: call_order.append("sleep"),
            ) as mock_sleep,
            patch(
                f"{MODULE}.requests.get",
                side_effect=lambda *a, **k: call_order.append("get") or _response(),
            ),
        ):
            fetcher.fetch("食べる", "たべる")

        mock_sleep.assert_called_once_with(0.7)
        assert call_order == ["sleep", "get"]

    def test_filename_sanitized_for_unsafe_characters(self, tmp_path):
        """Words containing path-hostile characters still cache safely."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.requests.get", return_value=_response()):
            result = fetcher.fetch("a/b:c", "x\\y")

        assert result is not None
        assert result.parent == tmp_path
        assert "/" not in result.name
        assert ":" not in result.name
        assert "\\" not in result.name

    def test_cache_dir_created_lazily_on_first_fetch(self, tmp_path):
        """mkdir is NOT called in __init__; it runs lazily inside fetch()."""
        cache_dir = tmp_path / "deep" / "nested" / "cache"
        assert not cache_dir.exists()
        fetcher = JPod101AudioFetcher(cache_dir=cache_dir, delay=0)
        assert not cache_dir.exists(), "mkdir must not be called in __init__"

        with patch(f"{MODULE}.requests.get", return_value=_response()):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert cache_dir.exists()
        assert result.parent == cache_dir

    def test_write_bytes_oserror_returns_none_no_files_left(self, tmp_path):
        """If writing the mp3 raises OSError, fetch returns None and leaves no files."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with (
            patch(f"{MODULE}.requests.get", return_value=_response()),
            patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")),
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))
        assert not list(tmp_path.glob("*.part"))

    def test_empty_body_200_returns_none_no_files(self, tmp_path):
        """Zero-byte 200 response is a transient failure — no mp3, no miss marker."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.requests.get", return_value=_response(content=b"")):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))

    def test_successful_write_leaves_no_part_file_and_correct_content(self, tmp_path):
        """Atomic write: no .part file remains after success and content is correct."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        audio = b"ID3" + b"\x00" * 200
        with patch(f"{MODULE}.requests.get", return_value=_response(content=audio)):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.read_bytes() == audio
        assert not list(tmp_path.glob("*.part"))

    def test_not_found_hash_constant_value(self):
        """The placeholder hash matches the value Yomitan hardcodes."""
        assert JPOD101_NOT_FOUND_SHA256 == "ae6398b5a27bc8c0a771df6c907ade794be15518174773c58c7c7ddd17098906"

    # ------------------------------------------------------------------
    # New hardening tests (TDD: these are written before implementation)
    # ------------------------------------------------------------------

    def test_html_body_returns_none_no_mp3_no_miss(self, tmp_path):
        """HTML error body (e.g. rate-limit page) is not cached as audio or miss.

        Non-audio bodies must be treated as transient failures so the word
        can be retried; writing a .miss marker here would permanently suppress
        a word that was only blocked by a rate-limit.
        """
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        html = b"<html>Too many requests</html>"
        with patch(f"{MODULE}.requests.get", return_value=_response(content=html)):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))

    def test_oversized_body_returns_none_nothing_written(self, tmp_path):
        """Body exceeding MAX_AUDIO_BYTES is rejected as a transient failure."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        # Build a mock whose iter_content yields bytes just over the cap.
        oversized = b"ID3" + b"\x00" * (MAX_AUDIO_BYTES + 1)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://assets.languagepod101.com/dictionary/japanese/audiomp3.php"
        # Yield in two chunks so the cap is hit mid-stream.
        half = MAX_AUDIO_BYTES // 2 + 10
        mock_resp.iter_content.side_effect = lambda chunk_size=8192: iter([oversized[:half], oversized[half:]])
        with patch(f"{MODULE}.requests.get", return_value=mock_resp):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))

    def test_http_final_url_returns_none_nothing_written(self, tmp_path):
        """If the final URL after redirects is plain HTTP, treat as transient failure.

        A redirect that downgrades from HTTPS to HTTP could expose audio
        data in transit; reject silently and retry next run.
        """
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(
            f"{MODULE}.requests.get",
            return_value=_response(url="http://cdn.example.com/audio.mp3"),
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))

    def test_id3_body_cached_successfully(self, tmp_path):
        """Body starting with ID3 tag is accepted and cached."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        id3_body = b"ID3" + b"\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 100
        with patch(f"{MODULE}.requests.get", return_value=_response(content=id3_body)):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.suffix == ".mp3"
        assert result.read_bytes() == id3_body

    def test_mpeg_frame_sync_body_cached_successfully(self, tmp_path):
        """Body starting with MPEG frame-sync bytes (0xFF 0xFB...) is accepted."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        # 0xFF 0xFB: top 3 bits of second byte = 0b111 = 0xE0 set
        mpeg_body = b"\xff\xfb\x90\x00" + b"\x00" * 100
        with patch(f"{MODULE}.requests.get", return_value=_response(content=mpeg_body)):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.suffix == ".mp3"
        assert result.read_bytes() == mpeg_body

    def test_placeholder_hash_still_writes_miss_with_new_checks(self, tmp_path):
        """Placeholder SHA still writes .miss even after new HTTPS/size/magic checks."""
        placeholder = b"placeholder-audio-bytes"
        digest = hashlib.sha256(placeholder).hexdigest()
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with (
            patch(f"{MODULE}.JPOD101_NOT_FOUND_SHA256", digest),
            patch(f"{MODULE}.requests.get", return_value=_response(content=placeholder)),
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        miss_files = list(tmp_path.glob("*.miss"))
        assert len(miss_files) == 1
        assert not list(tmp_path.glob("*.mp3"))

    # ------------------------------------------------------------------
    # response.close() leak tests — connection pool safety
    # ------------------------------------------------------------------

    def test_close_called_on_non_200_response(self, tmp_path):
        """response.close() must be called even when status != 200."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        resp = _response(status_code=503)
        with patch(f"{MODULE}.requests.get", return_value=resp):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        resp.close.assert_called_once()

    def test_close_called_on_http_final_url(self, tmp_path):
        """response.close() must be called when the final URL is plain HTTP."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        resp = _response(url="http://cdn.example.com/audio.mp3")
        with patch(f"{MODULE}.requests.get", return_value=resp):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        resp.close.assert_called_once()

    def test_close_called_on_oversized_body(self, tmp_path):
        """response.close() must be called when body exceeds MAX_AUDIO_BYTES."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        oversized = b"ID3" + b"\x00" * (MAX_AUDIO_BYTES + 1)
        half = MAX_AUDIO_BYTES // 2 + 10
        resp = MagicMock()
        resp.status_code = 200
        resp.url = "https://assets.languagepod101.com/dictionary/japanese/audiomp3.php"
        resp.iter_content.side_effect = lambda chunk_size=8192: iter([oversized[:half], oversized[half:]])
        with patch(f"{MODULE}.requests.get", return_value=resp):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        resp.close.assert_called_once()

    def test_close_called_on_success(self, tmp_path):
        """response.close() must be called even on a fully successful fetch."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        resp = _response()
        with patch(f"{MODULE}.requests.get", return_value=resp):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        resp.close.assert_called_once()

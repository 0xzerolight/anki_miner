"""Tests for JPod101AudioFetcher and ChainedExpressionAudioFetcher."""

import hashlib
import logging
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from anki_miner.services.expression_audio_fetcher import (
    FAILURE_KEYS,
    JPOD101_NOT_FOUND_SHA256,
    MAX_AUDIO_BYTES,
    STALE_PART_AGE_SECONDS,
    ChainedExpressionAudioFetcher,
    JPod101AudioFetcher,
    _classify_request_exception,
    _first_candidate_hit,
    purge_miss_markers,
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

    def test_session_sets_browser_user_agent(self, tmp_path):
        """The CDN 403s the default python-requests UA, so a browser UA is set.

        Without this, valid words 301-redirect to a CDN that rejects
        python-requests with 403 + HTML, every fetch returns None via the
        transient path, and audio silently falls through to a synthetic source.
        """
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        ua = fetcher._session.headers.get("User-Agent")
        assert ua
        assert not ua.lower().startswith("python-requests")

    def test_fetch_success_writes_mp3_and_returns_path(self, tmp_path):
        """Successful fetch downloads, caches, and returns the mp3 path."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response()) as mock_get:
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
            patch("requests.Session.get", return_value=_response(content=placeholder)),
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
        with patch("requests.Session.get", side_effect=requests.exceptions.Timeout):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.miss"))
        assert not list(tmp_path.glob("*.mp3"))

    def test_request_exception_returns_none_without_miss_marker(self, tmp_path):
        """Connection errors are swallowed and do not poison the cache."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", side_effect=requests.RequestException):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.miss"))
        assert not list(tmp_path.glob("*.mp3"))

    def test_non_200_returns_none_without_miss_marker(self, tmp_path):
        """Transient server errors must not write a miss marker."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response(status_code=503)):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.miss"))
        assert not list(tmp_path.glob("*.mp3"))

    def test_cache_hit_skips_network_and_sleep(self, tmp_path):
        """Existing non-empty mp3 short-circuits without network or delay."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0.2)
        with (
            patch("requests.Session.get", return_value=_response()),
            patch(f"{MODULE}.time.sleep"),
        ):
            first = fetcher.fetch("食べる", "たべる")
        assert first is not None

        with (
            patch("requests.Session.get") as mock_get,
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
            patch("requests.Session.get", return_value=_response(content=placeholder)),
        ):
            fetcher.fetch("食べる", "たべる")

        with (
            patch("requests.Session.get") as mock_get,
            patch(f"{MODULE}.time.sleep") as mock_sleep,
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        mock_get.assert_not_called()
        mock_sleep.assert_not_called()

    def test_empty_reading_returns_none_without_network(self, tmp_path):
        """Empty reading short-circuits to None: kana omitted → endpoint guesses
        a homograph reading, which would be cached permanently if wrong."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get") as mock_get:
            result = fetcher.fetch("食べる", "")

        assert result is None
        mock_get.assert_not_called()
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))

    def _write_miss(self, tmp_path):
        """Fetch once against the not-found placeholder to write a .miss marker."""
        placeholder = b"placeholder"
        digest = hashlib.sha256(placeholder).hexdigest()
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with (
            patch(f"{MODULE}.JPOD101_NOT_FOUND_SHA256", digest),
            patch("requests.Session.get", return_value=_response(content=placeholder)),
        ):
            fetcher.fetch("食べる", "たべる")
        miss_files = list(tmp_path.glob("*.miss"))
        assert len(miss_files) == 1
        return fetcher, miss_files[0]

    def test_fresh_miss_marker_within_ttl_skips_network(self, tmp_path):
        """A .miss younger than the TTL still short-circuits to None (no re-fetch)."""
        from anki_miner.services.expression_audio_fetcher import MISS_MARKER_TTL_SECONDS

        fetcher, miss = self._write_miss(tmp_path)
        # Age it to just inside the TTL window.
        recent = time.time() - (MISS_MARKER_TTL_SECONDS - 86400)
        os.utime(miss, (recent, recent))

        with patch("requests.Session.get") as mock_get:
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        mock_get.assert_not_called()

    def test_expired_miss_marker_refetches(self, tmp_path):
        """A .miss older than the TTL is expired: the word is re-requested."""
        from anki_miner.services.expression_audio_fetcher import MISS_MARKER_TTL_SECONDS

        fetcher, miss = self._write_miss(tmp_path)
        # Age the marker past the TTL; the word has since gained real audio.
        old = time.time() - (MISS_MARKER_TTL_SECONDS + 86400)
        os.utime(miss, (old, old))

        with patch("requests.Session.get", return_value=_response()) as mock_get:
            result = fetcher.fetch("食べる", "たべる")

        mock_get.assert_called_once()
        assert result is not None
        assert result.exists()

    def test_expired_miss_still_absent_resets_ttl(self, tmp_path):
        """Re-confirming an expired miss re-touches it, resetting the TTL clock."""
        from anki_miner.services.expression_audio_fetcher import MISS_MARKER_TTL_SECONDS

        fetcher, miss = self._write_miss(tmp_path)
        old = time.time() - (MISS_MARKER_TTL_SECONDS + 86400)
        os.utime(miss, (old, old))

        placeholder = b"placeholder"
        digest = hashlib.sha256(placeholder).hexdigest()
        with (
            patch(f"{MODULE}.JPOD101_NOT_FOUND_SHA256", digest),
            patch("requests.Session.get", return_value=_response(content=placeholder)) as mock_get,
        ):
            result = fetcher.fetch("食べる", "たべる")

        mock_get.assert_called_once()
        assert result is None
        # mtime refreshed → no longer expired.
        assert time.time() - miss.stat().st_mtime < MISS_MARKER_TTL_SECONDS

    def test_whitespace_only_reading_returns_none_without_network(self, tmp_path):
        """Whitespace-only reading is treated the same as empty."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get") as mock_get:
            result = fetcher.fetch("辛い", "   ")

        assert result is None
        mock_get.assert_not_called()
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))

    def test_empty_mined_form_returns_none_without_network(self, tmp_path):
        """Empty or whitespace mined_form short-circuits to None."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get") as mock_get:
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
                "requests.Session.get",
                side_effect=lambda *a, **k: call_order.append("get") or _response(),
            ),
        ):
            fetcher.fetch("食べる", "たべる")

        mock_sleep.assert_called_once_with(0.7)
        assert call_order == ["sleep", "get"]

    def test_filename_sanitized_for_unsafe_characters(self, tmp_path):
        """Words containing path-hostile characters still cache safely."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response()):
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

        with patch("requests.Session.get", return_value=_response()):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert cache_dir.exists()
        assert result.parent == cache_dir

    def test_write_oserror_returns_none_no_files_left(self, tmp_path):
        """If the atomic rename raises OSError, fetch returns None and cleans up the temp file."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with (
            patch("requests.Session.get", return_value=_response()),
            patch(f"{MODULE}.os.replace", side_effect=OSError("cross-device link")),
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))
        assert not list(tmp_path.glob("*.part"))

    def test_empty_body_200_returns_none_no_files(self, tmp_path):
        """Zero-byte 200 response is a transient failure — no mp3, no miss marker."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response(content=b"")):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))

    def test_successful_write_leaves_no_part_file_and_correct_content(self, tmp_path):
        """Atomic write: no .part file remains after success and content is correct."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        audio = b"ID3" + b"\x00" * 200
        with patch("requests.Session.get", return_value=_response(content=audio)):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.read_bytes() == audio
        assert not list(tmp_path.glob("*.part"))

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
        with patch("requests.Session.get", return_value=_response(content=html)):
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
        with patch("requests.Session.get", return_value=mock_resp):
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
            "requests.Session.get",
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
        with patch("requests.Session.get", return_value=_response(content=id3_body)):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.suffix == ".mp3"
        assert result.read_bytes() == id3_body

    def test_mpeg_frame_sync_body_cached_successfully(self, tmp_path):
        """Body starting with MPEG frame-sync bytes (0xFF 0xFB...) is accepted."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        # 0xFF 0xFB: top 3 bits of second byte = 0b111 = 0xE0 set
        mpeg_body = b"\xff\xfb\x90\x00" + b"\x00" * 100
        with patch("requests.Session.get", return_value=_response(content=mpeg_body)):
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
            patch("requests.Session.get", return_value=_response(content=placeholder)),
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
        with patch("requests.Session.get", return_value=resp):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        resp.close.assert_called_once()

    def test_close_called_on_http_final_url(self, tmp_path):
        """response.close() must be called when the final URL is plain HTTP."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        resp = _response(url="http://cdn.example.com/audio.mp3")
        with patch("requests.Session.get", return_value=resp):
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
        with patch("requests.Session.get", return_value=resp):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        resp.close.assert_called_once()

    def test_close_called_on_success(self, tmp_path):
        """response.close() must be called even on a fully successful fetch."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        resp = _response()
        with patch("requests.Session.get", return_value=resp):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        resp.close.assert_called_once()

    # ------------------------------------------------------------------
    # Negative delay clamping and error logging (Task 2)
    # ------------------------------------------------------------------

    def test_negative_delay_clamped_to_zero(self, tmp_path):
        """Negative delay from hand-edited config must not crash the run.

        Constructing with delay=-1 must clamp to 0.0 so time.sleep is never
        called with a negative argument (which raises ValueError).
        """
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=-1)
        assert fetcher._delay == 0.0
        sleep_calls: list[float] = []
        with (
            patch(f"{MODULE}.time.sleep", side_effect=lambda s: sleep_calls.append(s)),
            patch("requests.Session.get", return_value=_response()),
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert all(s >= 0.0 for s in sleep_calls), f"negative sleep arg: {sleep_calls}"

    def test_nan_delay_clamped_to_zero(self, tmp_path):
        """NaN delay must clamp to 0.0; max(0.0, nan) returns nan, so time.sleep
        would raise — the explicit guard must prevent that.
        """
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=float("nan"))
        assert fetcher._delay == 0.0

    def test_request_exception_emits_debug_log(self, tmp_path, caplog):
        """DNS/connection failure emits a debug log so failures are diagnosable."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with (
            caplog.at_level(logging.DEBUG, logger="anki_miner.services.expression_audio_fetcher"),
            patch(
                "requests.Session.get",
                side_effect=requests.exceptions.ConnectionError("Name or service not known"),
            ),
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert any(
            "expression audio" in r.message.lower() or "食べる" in r.message
            for r in caplog.records
            if r.levelno == logging.DEBUG
        ), f"No debug log emitted; records: {caplog.records}"

    # ------------------------------------------------------------------
    # Unique temp staging + stale .part sweep (Task 3)
    # ------------------------------------------------------------------

    def test_successful_fetch_leaves_no_part_files(self, tmp_path):
        """After a successful fetch no *.part files remain in the cache dir."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response()):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert not list(tmp_path.glob("*.part"))

    def test_stale_part_file_removed_before_fetch(self, tmp_path):
        """A .part file older than STALE_PART_AGE_SECONDS is deleted by the next fetch."""
        # Pre-seed a stale .part file.
        stale = tmp_path / "leftover.part"
        stale.write_bytes(b"garbage")
        old_time = time.time() - (STALE_PART_AGE_SECONDS + 10)
        os.utime(stale, (old_time, old_time))

        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response()):
            fetcher.fetch("食べる", "たべる")

        assert not stale.exists(), "stale .part file should have been removed"

    def test_fresh_part_file_not_removed(self, tmp_path):
        """A .part file with a current mtime is left alone (concurrent live download)."""
        # Pre-seed a fresh .part file (current mtime — within the guard window).
        fresh = tmp_path / "in_progress.part"
        fresh.write_bytes(b"in-flight data")
        # mtime defaults to now; explicitly set to ensure it is within threshold.
        now = time.time()
        os.utime(fresh, (now, now))

        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response()):
            fetcher.fetch("食べる", "たべる")

        assert fresh.exists(), "fresh .part file must not be removed"

    def test_warm_cache_fetch_skips_part_sweep(self, tmp_path):
        """A cache-hit fetch must NOT remove stale .part files.

        The sweep only runs on cold paths (after both cache-hit checks fail).
        A warm-cache hit returns before reaching the glob, so a stale .part
        file in the same directory must survive untouched.
        """
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        # Populate the cache with a valid mp3 first.
        with patch("requests.Session.get", return_value=_response()):
            first = fetcher.fetch("食べる", "たべる")
        assert first is not None

        # Plant a stale .part file after the cache is warm.
        stale = tmp_path / "orphan.part"
        stale.write_bytes(b"orphan")
        old_time = time.time() - (STALE_PART_AGE_SECONDS + 10)
        os.utime(stale, (old_time, old_time))

        # Warm-cache fetch — must NOT touch the stale .part.
        with (
            patch("requests.Session.get") as mock_get,
            patch(f"{MODULE}.time.sleep") as mock_sleep,
        ):
            second = fetcher.fetch("食べる", "たべる")

        assert second == first
        mock_get.assert_not_called()
        mock_sleep.assert_not_called()
        assert stale.exists(), "warm-cache hit must not remove stale .part files"

    def test_staging_uses_unique_temp_name_not_deterministic(self, tmp_path):
        """Staging goes through NamedTemporaryFile, not a deterministic .mp3.part path.

        Asserts that (a) the fetcher calls tempfile.NamedTemporaryFile and (b) the
        final cached mp3 contains the correct bytes — i.e. the unique-staging path
        executed successfully end-to-end.
        """
        import tempfile as _tempfile

        audio = b"ID3" + b"\x01\x02\x03" + b"\x00" * 50
        called_with_unique = []

        original_ntf = _tempfile.NamedTemporaryFile

        def recording_ntf(**kwargs):
            f = original_ntf(**kwargs)
            called_with_unique.append(f.name)
            return f

        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with (
            patch("requests.Session.get", return_value=_response(content=audio)),
            patch(f"{MODULE}.tempfile.NamedTemporaryFile", side_effect=recording_ntf),
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.read_bytes() == audio
        # NamedTemporaryFile was called at least once (unique staging used).
        assert len(called_with_unique) >= 1
        # The deterministic name must NOT have been used as the staging file.
        from anki_miner.utils.file_utils import safe_filename

        stem = safe_filename("jpod101_食べる_たべる")
        deterministic_part = str(tmp_path / f"{stem}.mp3.part")
        assert deterministic_part not in called_with_unique

    # ------------------------------------------------------------------
    # Cancellation hook (Task 5)
    # ------------------------------------------------------------------

    def test_cancelled_check_true_at_entry_returns_none_no_network(self, tmp_path):
        """cancelled_check returning True immediately ⇒ None, no network call, nothing written."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get") as mock_get:
            result = fetcher.fetch("食べる", "たべる", cancelled_check=lambda: True)

        assert result is None
        mock_get.assert_not_called()
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))

    def test_cancelled_check_false_proceeds_normally(self, tmp_path):
        """cancelled_check returning False ⇒ fetch proceeds and returns the cached path."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response()):
            result = fetcher.fetch("食べる", "たべる", cancelled_check=lambda: False)

        assert result is not None
        assert result.exists()
        assert result.suffix == ".mp3"

    def test_cancelled_check_none_default_unchanged_behavior(self, tmp_path):
        """Omitting cancelled_check (default None) leaves behavior unchanged."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response()):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.exists()

    def test_cancelled_check_true_before_sleep_returns_none_no_network(self, tmp_path):
        """cancelled_check checked before sleep — returns None without hitting network."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0.5)
        call_count = 0

        def _cancelled_after_first():
            nonlocal call_count
            call_count += 1
            # First call = entry guard (returns False), second call = pre-sleep guard (returns True)
            return call_count >= 2

        with (
            patch("requests.Session.get") as mock_get,
            patch(f"{MODULE}.time.sleep") as mock_sleep,
        ):
            result = fetcher.fetch("食べる", "たべる", cancelled_check=_cancelled_after_first)

        assert result is None
        mock_get.assert_not_called()
        mock_sleep.assert_not_called()

    def test_cancelled_check_true_before_request_returns_none(self, tmp_path):
        """cancelled_check checked before network request — returns None without fetching."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        call_count = 0

        def _cancelled_before_request():
            nonlocal call_count
            call_count += 1
            # entry=False, pre-sleep=False, pre-request=True
            return call_count >= 3

        with (
            patch("requests.Session.get") as mock_get,
            patch(f"{MODULE}.time.sleep"),
        ):
            result = fetcher.fetch("食べる", "たべる", cancelled_check=_cancelled_before_request)

        assert result is None
        mock_get.assert_not_called()
        assert not list(tmp_path.glob("*.miss"))

    # ------------------------------------------------------------------
    # Session reuse (Task 6)
    # ------------------------------------------------------------------

    def test_zero_byte_cached_mp3_refetched(self, tmp_path):
        """A zero-byte .mp3 in the cache dir triggers a network refetch and
        the file is repaired with the valid body.

        The cache-hit guard checks st_size > 0, so a truncated/empty mp3
        left by a previous crash must not satisfy the hit and must be
        replaced with valid content.
        """
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)

        # Pre-seed a zero-byte mp3 with the deterministic cache filename.
        from anki_miner.utils.file_utils import safe_filename

        stem = safe_filename("jpod101_食べる_たべる")
        empty_mp3 = tmp_path / f"{stem}.mp3"
        empty_mp3.write_bytes(b"")

        with patch("requests.Session.get", return_value=_response()) as mock_get:
            result = fetcher.fetch("食べる", "たべる")

        # Network must have been hit (cache miss due to empty file).
        mock_get.assert_called_once()
        assert result is not None
        assert result.read_bytes() == _VALID_MP3
        assert result.stat().st_size > 0

    def test_session_reused_across_fetches(self, tmp_path):
        """The same requests.Session.get is called for two distinct cold-cache words.

        Ensures that a single Session is created once and reused rather than
        opening a fresh TCP+TLS connection per word.
        """
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response()) as mock_session_get:
            fetcher.fetch("食べる", "たべる")
            fetcher.fetch("飲む", "のむ")

        assert (
            mock_session_get.call_count == 2
        ), f"expected 2 calls (one per word) but got {mock_session_get.call_count}"

    def test_not_found_hash_constant_value(self):
        """The placeholder hash matches the value Yomitan hardcodes."""
        assert JPOD101_NOT_FOUND_SHA256 == "ae6398b5a27bc8c0a771df6c907ade794be15518174773c58c7c7ddd17098906"


class TestChainedExpressionAudioFetcher:
    """Tests for ChainedExpressionAudioFetcher."""

    def _stub(self, return_value: Path | None) -> object:
        """Return a minimal stub fetcher that returns ``return_value``."""
        from collections.abc import Callable

        class _Stub:
            def __init__(self, rv: Path | None) -> None:
                self._rv = rv
                self.calls: list[tuple[str, str]] = []

            def fetch(
                self,
                mined_form: str,
                reading: str,
                cancelled_check: Callable[[], bool] | None = None,
            ) -> Path | None:
                self.calls.append((mined_form, reading))
                return self._rv

        return _Stub(return_value)

    def test_first_hit_returned_second_never_called(self, tmp_path):
        """When the first fetcher returns a Path, the second is not consulted."""
        audio = tmp_path / "word.mp3"
        audio.touch()
        first = self._stub(audio)
        second = self._stub(tmp_path / "other.mp3")
        chain = ChainedExpressionAudioFetcher([first, second])  # type: ignore[arg-type]

        result = chain.fetch("食べる", "たべる")

        assert result == audio
        assert len(first.calls) == 1  # type: ignore[union-attr]
        assert len(second.calls) == 0  # type: ignore[union-attr]

    def test_first_none_second_consulted_and_returned(self, tmp_path):
        """When the first fetcher returns None, the second is tried and its Path returned."""
        audio = tmp_path / "word.mp3"
        audio.touch()
        first = self._stub(None)
        second = self._stub(audio)
        chain = ChainedExpressionAudioFetcher([first, second])  # type: ignore[arg-type]

        result = chain.fetch("食べる", "たべる")

        assert result == audio
        assert len(first.calls) == 1  # type: ignore[union-attr]
        assert len(second.calls) == 1  # type: ignore[union-attr]

    def test_all_none_returns_none(self, tmp_path):
        """When every fetcher returns None, the chain returns None."""
        chain = ChainedExpressionAudioFetcher([self._stub(None), self._stub(None)])  # type: ignore[arg-type]

        result = chain.fetch("食べる", "たべる")

        assert result is None

    def test_empty_chain_returns_none(self):
        """An empty fetcher list returns None immediately."""
        chain = ChainedExpressionAudioFetcher([])

        result = chain.fetch("食べる", "たべる")

        assert result is None

    def test_cancelled_check_forwarded_to_members(self, tmp_path):
        """The chain forwards cancelled_check to each member fetcher."""
        received: list[object] = []

        class _Recorder:
            def fetch(self, mined_form, reading, cancelled_check=None):
                received.append(cancelled_check)
                return None

        def check() -> bool:
            return False

        chain = ChainedExpressionAudioFetcher([_Recorder(), _Recorder()])  # type: ignore[list-item]

        result = chain.fetch("食べる", "たべる", check)

        assert result is None
        assert received == [check, check]

    def test_cancelled_before_first_member_skips_all(self, tmp_path):
        """cancelled_check True at entry ⇒ None without consulting any member."""
        first = self._stub(tmp_path / "word.mp3")
        chain = ChainedExpressionAudioFetcher([first])  # type: ignore[arg-type]

        result = chain.fetch("食べる", "たべる", cancelled_check=lambda: True)

        assert result is None
        assert len(first.calls) == 0  # type: ignore[union-attr]

    def test_cancelled_between_members_short_circuits(self, tmp_path):
        """Cancellation observed between members stops the walk and returns None."""
        audio = tmp_path / "word.mp3"
        audio.touch()
        first = self._stub(None)
        second = self._stub(audio)
        calls = 0

        def _cancel_after_first() -> bool:
            nonlocal calls
            calls += 1
            # First consultation (before member 1) → False; second → True.
            return calls >= 2

        chain = ChainedExpressionAudioFetcher([first, second])  # type: ignore[arg-type]

        result = chain.fetch("食べる", "たべる", cancelled_check=_cancel_after_first)

        assert result is None
        assert len(first.calls) == 1  # type: ignore[union-attr]
        assert len(second.calls) == 0  # type: ignore[union-attr]


class _CandidateStub:
    """Stub fetcher: returns the mapped Path for matching ``(form, reading)`` pairs.

    Implements both ``fetch`` (records every call) and ``fetch_candidates``
    (delegates to the production helper) so it behaves like a real leaf.
    ``hits=None`` means "hit on ANY candidate" (the synthetic-fallback shape).
    """

    def __init__(self, path, hits=None):
        self._path = path
        self._hits = hits  # set of (form, reading), or None = match anything
        self.calls: list[tuple[str, str]] = []

    def fetch(self, mined_form, reading, cancelled_check=None):
        self.calls.append((mined_form, reading))
        if self._hits is None or (mined_form, reading) in self._hits:
            return self._path
        return None

    def fetch_candidates(self, candidates, cancelled_check=None):
        return _first_candidate_hit(self, candidates, cancelled_check)


class TestFetchCandidates:
    """Source-priority-outer / candidate-ladder-inner fetch_candidates."""

    def test_leaf_tries_candidates_in_order_first_hit_wins(self, tmp_path):
        """A leaf tries each candidate in order and returns on the first hit."""
        audio = tmp_path / "lemma.mp3"
        stub = _CandidateStub(audio, hits={("嘘", "うそ")})

        result = stub.fetch_candidates([("噓", "うそ"), ("嘘", "うそ")])

        assert result == audio
        # Surface tried first (miss), then lemma (hit); no calls after the hit.
        assert stub.calls == [("噓", "うそ"), ("嘘", "うそ")]

    def test_leaf_all_miss_returns_none(self, tmp_path):
        """A leaf that hits nothing returns None after trying every candidate."""
        stub = _CandidateStub(tmp_path / "x.mp3", hits=set())

        result = stub.fetch_candidates([("噓", "うそ"), ("嘘", "うそ")])

        assert result is None
        assert stub.calls == [("噓", "うそ"), ("嘘", "うそ")]

    def test_leaf_empty_candidates_returns_none_without_fetch(self, tmp_path):
        """An empty candidate ladder is a no-op — fetch is never called."""
        stub = _CandidateStub(tmp_path / "x.mp3")

        result = stub.fetch_candidates([])

        assert result is None
        assert stub.calls == []

    def test_higher_priority_lemma_beats_lower_priority_surface(self, tmp_path):
        """Regression for the inverted-nesting bug (Issue: JPod101 never used).

        JPod101 misses the surface form but HAS the lemma; Google TTS (synthetic)
        would hit the surface form.  Source priority must dominate: JPod101 must
        try its lemma candidate BEFORE the chain ever falls through to googletts.
        Before the fix, googletts satisfied the surface candidate first and
        JPod101's lemma was never reached.
        """
        jpod_audio = tmp_path / "jpod101_嘘_うそ.mp3"
        gtts_audio = tmp_path / "googletts_噓_うそ.mp3"
        jpod = _CandidateStub(jpod_audio, hits={("嘘", "うそ")})  # only the lemma
        googletts = _CandidateStub(gtts_audio, hits=None)  # any candidate (synthetic)
        chain = ChainedExpressionAudioFetcher([jpod, googletts])  # type: ignore[list-item]

        candidates = [("噓", "うそ"), ("嘘", "うそ")]  # surface, then lemma
        result = chain.fetch_candidates(candidates)

        assert result == jpod_audio
        # JPod101 tried BOTH forms before the chain moved on.
        assert jpod.calls == [("噓", "うそ"), ("嘘", "うそ")]
        # Google TTS was never consulted — JPod101's lemma won.
        assert googletts.calls == []

    def test_falls_through_to_next_source_when_first_misses_all(self, tmp_path):
        """When the first source misses every candidate, the next source is tried."""
        gtts_audio = tmp_path / "googletts.mp3"
        jpod = _CandidateStub(tmp_path / "never.mp3", hits=set())  # misses everything
        googletts = _CandidateStub(gtts_audio, hits=None)
        chain = ChainedExpressionAudioFetcher([jpod, googletts])  # type: ignore[list-item]

        candidates = [("噓", "うそ"), ("嘘", "うそ")]
        result = chain.fetch_candidates(candidates)

        assert result == gtts_audio
        assert jpod.calls == [("噓", "うそ"), ("嘘", "うそ")]  # all candidates tried
        assert googletts.calls == [("噓", "うそ")]  # first candidate hits

    def test_first_source_first_candidate_short_circuits(self, tmp_path):
        """A hit on the first source's first candidate consults nothing further."""
        audio = tmp_path / "hit.mp3"
        jpod = _CandidateStub(audio, hits=None)
        googletts = _CandidateStub(tmp_path / "other.mp3", hits=None)
        chain = ChainedExpressionAudioFetcher([jpod, googletts])  # type: ignore[list-item]

        result = chain.fetch_candidates([("噓", "うそ"), ("嘘", "うそ")])

        assert result == audio
        assert jpod.calls == [("噓", "うそ")]
        assert googletts.calls == []

    def test_empty_chain_returns_none(self):
        """An empty source chain returns None."""
        chain = ChainedExpressionAudioFetcher([])

        assert chain.fetch_candidates([("噓", "うそ")]) is None

    def test_cancelled_before_first_source_skips_all(self, tmp_path):
        """cancelled_check True at entry ⇒ None, no source consulted."""
        jpod = _CandidateStub(tmp_path / "x.mp3", hits=None)
        chain = ChainedExpressionAudioFetcher([jpod])  # type: ignore[list-item]

        result = chain.fetch_candidates([("噓", "うそ")], cancelled_check=lambda: True)

        assert result is None
        assert jpod.calls == []

    def test_cancelled_between_candidates_in_leaf(self, tmp_path):
        """A leaf stops its ladder when cancellation is observed between candidates."""
        stub = _CandidateStub(tmp_path / "x.mp3", hits=set())
        calls = 0

        def _cancel_after_first() -> bool:
            nonlocal calls
            calls += 1
            return calls >= 2  # False before candidate 1, True before candidate 2

        result = stub.fetch_candidates([("噓", "うそ"), ("嘘", "うそ")], cancelled_check=_cancel_after_first)

        assert result is None
        assert stub.calls == [("噓", "うそ")]  # second candidate never tried

    def test_jpod101_fetch_candidates_delegates_per_form(self, tmp_path):
        """JPod101AudioFetcher.fetch_candidates tries each form via its own fetch."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        audio = tmp_path / "jpod101_嘘_うそ.mp3"

        def _fake_fetch(mined_form, reading, cancelled_check=None):
            return audio if (mined_form, reading) == ("嘘", "うそ") else None

        with patch.object(fetcher, "fetch", side_effect=_fake_fetch) as mock_fetch:
            result = fetcher.fetch_candidates([("噓", "うそ"), ("嘘", "うそ")])

        assert result == audio
        assert [c.args[:2] for c in mock_fetch.call_args_list] == [
            ("噓", "うそ"),
            ("嘘", "うそ"),
        ]


class TestExpressionAudioClose:
    """Tests for the resource-release close() methods (Windows freeze fix)."""

    def test_jpod101_close_closes_session(self, tmp_path):
        """JPod101AudioFetcher.close() closes its requests.Session."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch.object(fetcher._session, "close") as mock_close:
            fetcher.close()
        mock_close.assert_called_once_with()

    def test_chained_close_fans_out_to_all_members(self, tmp_path):
        """ChainedExpressionAudioFetcher.close() calls close() on every member."""
        first = MagicMock()
        second = MagicMock()
        chain = ChainedExpressionAudioFetcher([first, second])  # type: ignore[list-item]

        chain.close()

        first.close.assert_called_once_with()
        second.close.assert_called_once_with()

    def test_chained_close_tolerates_member_without_close(self, tmp_path):
        """A member lacking close() is skipped, not an error."""

        class _NoClose:
            def fetch(self, mined_form, reading, cancelled_check=None):
                return None

            def fetch_candidates(self, candidates, cancelled_check=None):
                return None

        closable = MagicMock()
        chain = ChainedExpressionAudioFetcher([_NoClose(), closable])  # type: ignore[list-item]

        chain.close()  # must not raise

        closable.close.assert_called_once_with()

    def test_chained_close_suppresses_member_exception(self, tmp_path):
        """A member close() that raises does not abort the fan-out."""
        boom = MagicMock()
        boom.close.side_effect = RuntimeError("boom")
        after = MagicMock()
        chain = ChainedExpressionAudioFetcher([boom, after])  # type: ignore[list-item]

        chain.close()  # must not raise

        after.close.assert_called_once_with()


class TestClassifyRequestException:
    """Failure-bucket mapping for raised exceptions (order-sensitive)."""

    def test_ssl_error_maps_to_ssl_even_though_connection_subclass(self):
        """SSLError subclasses ConnectionError, so it must be checked first."""
        assert issubclass(requests.exceptions.SSLError, requests.exceptions.ConnectionError)
        assert _classify_request_exception(requests.exceptions.SSLError("expired")) == "ssl"

    def test_timeout_maps_to_timeout(self):
        assert _classify_request_exception(requests.exceptions.Timeout()) == "timeout"

    def test_connect_timeout_prefers_timeout_over_connection(self):
        """ConnectTimeout subclasses both; timeout is the more specific bucket."""
        assert _classify_request_exception(requests.exceptions.ConnectTimeout()) == "timeout"

    def test_connection_error_maps_to_connection(self):
        assert _classify_request_exception(requests.exceptions.ConnectionError()) == "connection"

    def test_generic_request_exception_falls_to_connection(self):
        assert _classify_request_exception(requests.RequestException()) == "connection"

    def test_oserror_falls_to_connection(self):
        assert _classify_request_exception(OSError("nope")) == "connection"


class TestJPod101FailureStats:
    """Per-run failure-cause counters bumped in the transient branches."""

    def test_fresh_fetcher_has_zeroed_counts(self, tmp_path):
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        assert fetcher.stats() == dict.fromkeys(FAILURE_KEYS, 0)

    def test_stats_returns_a_copy(self, tmp_path):
        """Mutating the returned dict must not corrupt the live tally."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        snapshot = fetcher.stats()
        snapshot["ssl"] = 999
        assert fetcher.stats()["ssl"] == 0

    def test_non_200_bumps_http_status(self, tmp_path):
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response(status_code=503)):
            assert fetcher.fetch("食べる", "たべる") is None
        assert fetcher.stats()["http_status"] == 1

    def test_non_audio_body_bumps_non_audio(self, tmp_path):
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response(content=b"<html>rate limit</html>")):
            assert fetcher.fetch("食べる", "たべる") is None
        assert fetcher.stats()["non_audio"] == 1

    def test_oversized_body_bumps_non_audio(self, tmp_path):
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        oversized = b"\xff\xfb" + b"\x00" * (MAX_AUDIO_BYTES + 1)
        with patch("requests.Session.get", return_value=_response(content=oversized)):
            assert fetcher.fetch("食べる", "たべる") is None
        assert fetcher.stats()["non_audio"] == 1

    def test_empty_body_bumps_connection(self, tmp_path):
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response(content=b"")):
            assert fetcher.fetch("食べる", "たべる") is None
        assert fetcher.stats()["connection"] == 1

    def test_https_downgrade_bumps_connection(self, tmp_path):
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        resp = _response(url="http://insecure.example/audio.mp3")
        with patch("requests.Session.get", return_value=resp):
            assert fetcher.fetch("食べる", "たべる") is None
        assert fetcher.stats()["connection"] == 1

    def test_timeout_bumps_timeout(self, tmp_path):
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", side_effect=requests.exceptions.Timeout):
            assert fetcher.fetch("食べる", "たべる") is None
        assert fetcher.stats()["timeout"] == 1

    def test_ssl_error_bumps_ssl(self, tmp_path):
        """The expired-certificate case — the whole point of the feature."""
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(
            "requests.Session.get",
            side_effect=requests.exceptions.SSLError("certificate has expired"),
        ):
            assert fetcher.fetch("食べる", "たべる") is None
        assert fetcher.stats()["ssl"] == 1
        assert fetcher.stats()["connection"] == 0

    def test_connection_error_bumps_connection(self, tmp_path):
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(
            "requests.Session.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            assert fetcher.fetch("食べる", "たべる") is None
        assert fetcher.stats()["connection"] == 1

    def test_confirmed_miss_bumps_nothing(self, tmp_path):
        """A genuine not-found placeholder is a miss, not a failure."""
        placeholder = b"not-found-placeholder-body"
        with patch.object(hashlib, "sha256") as mock_sha:
            mock_sha.return_value.hexdigest.return_value = JPOD101_NOT_FOUND_SHA256
            fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
            with patch("requests.Session.get", return_value=_response(content=placeholder)):
                assert fetcher.fetch("食べる", "たべる") is None
        assert fetcher.stats() == dict.fromkeys(FAILURE_KEYS, 0)

    def test_successful_fetch_bumps_nothing(self, tmp_path):
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", return_value=_response()):
            assert fetcher.fetch("食べる", "たべる") is not None
        assert fetcher.stats() == dict.fromkeys(FAILURE_KEYS, 0)

    def test_repeated_failures_accumulate(self, tmp_path):
        fetcher = JPod101AudioFetcher(cache_dir=tmp_path, delay=0)
        with patch("requests.Session.get", side_effect=requests.exceptions.SSLError):
            fetcher.fetch("食べる", "たべる")
            fetcher.fetch("飲む", "のむ")
        assert fetcher.stats()["ssl"] == 2


class TestChainedFailureStats:
    """Chained fetcher aggregates member stats() (duck-typed like close())."""

    def test_aggregates_across_members(self):
        first = MagicMock()
        first.stats.return_value = {"ssl": 3, "connection": 1, "timeout": 0, "http_status": 0, "non_audio": 0}
        second = MagicMock()
        second.stats.return_value = {"ssl": 1, "connection": 0, "timeout": 2, "http_status": 0, "non_audio": 5}
        chain = ChainedExpressionAudioFetcher([first, second])

        totals = chain.stats()

        assert totals == {"ssl": 4, "connection": 1, "timeout": 2, "http_status": 0, "non_audio": 5}

    def test_skips_member_without_stats(self):
        class _NoStats:
            def fetch(self, mined_form, reading, cancelled_check=None):
                return None

            def fetch_candidates(self, candidates, cancelled_check=None):
                return None

        counted = MagicMock()
        counted.stats.return_value = {"ssl": 2, "connection": 0, "timeout": 0, "http_status": 0, "non_audio": 0}
        chain = ChainedExpressionAudioFetcher([_NoStats(), counted])  # type: ignore[list-item]

        assert chain.stats()["ssl"] == 2

    def test_ignores_unknown_member_keys(self):
        weird = MagicMock()
        weird.stats.return_value = {"ssl": 1, "made_up": 99}
        chain = ChainedExpressionAudioFetcher([weird])

        totals = chain.stats()

        assert totals["ssl"] == 1
        assert "made_up" not in totals

    def test_suppresses_member_stats_exception(self):
        boom = MagicMock()
        boom.stats.side_effect = RuntimeError("boom")
        after = MagicMock()
        after.stats.return_value = {"ssl": 7, "connection": 0, "timeout": 0, "http_status": 0, "non_audio": 0}
        chain = ChainedExpressionAudioFetcher([boom, after])

        totals = chain.stats()  # must not raise

        assert totals["ssl"] == 7

    def test_empty_chain_returns_zeroed_counts(self):
        chain = ChainedExpressionAudioFetcher([])
        assert chain.stats() == dict.fromkeys(FAILURE_KEYS, 0)


class TestPurgeMissMarkers:
    """Tests for the purge_miss_markers cache-hygiene helper."""

    def test_removes_all_miss_markers_and_returns_count(self, tmp_path):
        (tmp_path / "a.miss").touch()
        (tmp_path / "b.miss").touch()
        (tmp_path / "keep.mp3").write_bytes(b"ID3keep")

        removed = purge_miss_markers(tmp_path)

        assert removed == 2
        assert not list(tmp_path.glob("*.miss"))
        assert (tmp_path / "keep.mp3").exists()

    def test_missing_directory_returns_zero(self, tmp_path):
        assert purge_miss_markers(tmp_path / "does_not_exist") == 0

    def test_empty_directory_returns_zero(self, tmp_path):
        assert purge_miss_markers(tmp_path) == 0

    def test_unlink_error_is_skipped(self, tmp_path):
        (tmp_path / "a.miss").touch()
        (tmp_path / "b.miss").touch()

        real_unlink = Path.unlink

        def flaky_unlink(self, *args, **kwargs):
            if self.name == "a.miss":
                raise OSError("locked")
            return real_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", flaky_unlink):
            removed = purge_miss_markers(tmp_path)

        assert removed == 1
        assert (tmp_path / "a.miss").exists()
        assert not (tmp_path / "b.miss").exists()

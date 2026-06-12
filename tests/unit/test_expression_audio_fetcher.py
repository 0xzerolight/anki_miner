"""Tests for JPod101AudioFetcher and ChainedExpressionAudioFetcher."""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from anki_miner.services.expression_audio_fetcher import (
    JPOD101_NOT_FOUND_SHA256,
    ChainedExpressionAudioFetcher,
    JPod101AudioFetcher,
)

MODULE = "anki_miner.services.expression_audio_fetcher"


def _response(status_code: int = 200, content: bytes = b"mp3-bytes") -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.content = content
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
        assert result.read_bytes() == b"mp3-bytes"
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
        with patch(f"{MODULE}.requests.get", return_value=_response(content=b"audio-data")):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.read_bytes() == b"audio-data"
        assert not list(tmp_path.glob("*.part"))

    def test_not_found_hash_constant_value(self):
        """The placeholder hash matches the value Yomitan hardcodes."""
        assert JPOD101_NOT_FOUND_SHA256 == "ae6398b5a27bc8c0a771df6c907ade794be15518174773c58c7c7ddd17098906"


class TestChainedExpressionAudioFetcher:
    """Tests for ChainedExpressionAudioFetcher."""

    def _stub(self, return_value: Path | None) -> object:
        """Return a minimal stub fetcher that returns ``return_value``."""

        class _Stub:
            def __init__(self, rv: Path | None) -> None:
                self._rv = rv
                self.calls: list[tuple[str, str]] = []

            def fetch(self, mined_form: str, reading: str) -> Path | None:
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

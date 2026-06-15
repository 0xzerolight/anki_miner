"""Tests for GoogleTranslateAudioFetcher (synthetic TTS expression audio)."""

import logging
from unittest.mock import patch

from anki_miner.services.expression_audio_fetcher import MAX_AUDIO_BYTES
from anki_miner.services.google_translate_audio_fetcher import GoogleTranslateAudioFetcher

MODULE = "anki_miner.services.google_translate_audio_fetcher"

# Minimal valid ID3v2-tagged MP3 body for success-path tests.
_VALID_MP3 = b"ID3" + b"\x00" * 7 + b"\xff\xfb\x90\x00" + b"\x00" * 100


def _gtts_stub(body: bytes):
    """Build a fake gTTS class whose instances write *body* via write_to_fp.

    Returns (FakeGTTS, calls) where ``calls`` records each constructor kwargs
    dict so tests can assert on the text/lang fed to gTTS.
    """
    calls: list[dict] = []

    class _FakeGTTS:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)

        def write_to_fp(self, fp):
            fp.write(body)

    return _FakeGTTS, calls


class TestGoogleTranslateAudioFetcher:
    """Behavior tests for GoogleTranslateAudioFetcher."""

    def test_fetch_success_writes_mp3_and_returns_path(self, tmp_path):
        """Successful synthesis caches the body and returns the mp3 path."""
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.exists()
        assert result.suffix == ".mp3"
        assert result.read_bytes() == _VALID_MP3
        assert result.parent == tmp_path
        assert result.name.startswith("googletts_")

    def test_synthesizes_reading_not_kanji(self, tmp_path):
        """gTTS is fed the kana reading and lang='ja', never the kanji."""
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            fetcher.fetch("辛い", "つらい")

        assert len(calls) == 1
        assert calls[0]["text"] == "つらい"
        assert calls[0]["lang"] == "ja"

    def test_cache_hit_skips_second_synthesis(self, tmp_path):
        """A warm-cache hit returns the same path without calling gTTS again."""
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            first = fetcher.fetch("食べる", "たべる")
        assert first is not None
        assert len(calls) == 1

        # Second call: gTTS must not be constructed at all.
        with patch(f"{MODULE}.gtts.gTTS", side_effect=AssertionError("synth re-run")):
            second = fetcher.fetch("食べる", "たべる")

        assert second == first

    def test_empty_reading_returns_none_no_synthesis(self, tmp_path):
        """Empty reading short-circuits to None (homograph guard); no gTTS call."""
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("食べる", "")

        assert result is None
        assert calls == []
        assert not list(tmp_path.glob("*.mp3"))

    def test_whitespace_reading_returns_none_no_synthesis(self, tmp_path):
        """Whitespace-only reading is treated as empty."""
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("辛い", "   ")

        assert result is None
        assert calls == []

    def test_empty_mined_form_returns_none_no_synthesis(self, tmp_path):
        """Empty or whitespace mined_form short-circuits to None."""
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            assert fetcher.fetch("", "たべる") is None
            assert fetcher.fetch("   ", "たべる") is None

        assert calls == []

    def test_gtts_raising_returns_none(self, tmp_path):
        """A gTTS error is swallowed: fetch returns None and never raises."""
        err_cls: type[Exception] = RuntimeError
        try:
            from gtts.tts import gTTSError  # type: ignore  # noqa: N813

            err_cls = gTTSError
        except Exception:  # pragma: no cover - fallback if import path changes
            pass

        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", side_effect=err_cls("429 rate limited")):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))
        # No negative-cache marker is written.
        assert not list(tmp_path.glob("*.miss"))

    def test_no_miss_marker_after_failure_allows_retry(self, tmp_path):
        """A failed synthesis writes no marker, so the next run retries and succeeds."""
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", side_effect=RuntimeError("transient")):
            assert fetcher.fetch("食べる", "たべる") is None

        fake, calls = _gtts_stub(_VALID_MP3)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert len(calls) == 1

    def test_cancelled_check_true_returns_none_no_synthesis(self, tmp_path):
        """cancelled_check True at entry ⇒ None, no file, no gTTS call."""
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("食べる", "たべる", cancelled_check=lambda: True)

        assert result is None
        assert calls == []
        assert not list(tmp_path.glob("*.mp3"))

    def test_non_mp3_body_returns_none_nothing_cached(self, tmp_path):
        """A non-MP3 body is rejected; nothing is cached."""
        fake, _ = _gtts_stub(b"<html>not audio</html>")
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))

    def test_empty_body_returns_none(self, tmp_path):
        """An empty synthesized body is a transient failure — None, nothing written."""
        fake, _ = _gtts_stub(b"")
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))

    def test_oversized_body_returns_none_nothing_written(self, tmp_path):
        """Body exceeding MAX_AUDIO_BYTES is rejected as a transient failure."""
        oversized = b"ID3" + b"\x00" * (MAX_AUDIO_BYTES + 1)
        fake, _ = _gtts_stub(oversized)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))

    def test_id3_body_cached_successfully(self, tmp_path):
        """A body starting with an ID3 tag is accepted and cached."""
        id3_body = b"ID3" + b"\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 100
        fake, _ = _gtts_stub(id3_body)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.suffix == ".mp3"
        assert result.read_bytes() == id3_body

    def test_mpeg_frame_sync_body_cached_successfully(self, tmp_path):
        """A body starting with MPEG frame-sync bytes is accepted."""
        mpeg_body = b"\xff\xfb\x90\x00" + b"\x00" * 100
        fake, _ = _gtts_stub(mpeg_body)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.read_bytes() == mpeg_body

    def test_filename_sanitized_for_unsafe_characters(self, tmp_path):
        """Words with path-hostile characters still cache safely."""
        fake, _ = _gtts_stub(_VALID_MP3)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("a/b:c", "x\\y")

        assert result is not None
        assert result.parent == tmp_path
        assert "/" not in result.name
        assert ":" not in result.name
        assert "\\" not in result.name

    def test_cache_dir_created_lazily_on_first_fetch(self, tmp_path):
        """mkdir runs lazily inside fetch(), not in __init__."""
        cache_dir = tmp_path / "deep" / "nested" / "cache"
        fetcher = GoogleTranslateAudioFetcher(cache_dir=cache_dir, delay=0)
        assert not cache_dir.exists(), "mkdir must not run in __init__"

        fake, _ = _gtts_stub(_VALID_MP3)
        with patch(f"{MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert cache_dir.exists()
        assert result.parent == cache_dir

    def test_write_oserror_returns_none_no_files_left(self, tmp_path):
        """If the atomic rename raises OSError, fetch returns None and cleans up."""
        fake, _ = _gtts_stub(_VALID_MP3)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with (
            patch(f"{MODULE}.gtts.gTTS", fake),
            patch(f"{MODULE}.os.replace", side_effect=OSError("cross-device link")),
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.part"))

    def test_delay_applied_before_synthesis(self, tmp_path):
        """time.sleep is called with the constructor delay before synthesis."""
        fake, _ = _gtts_stub(_VALID_MP3)
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0.7)
        with (
            patch(f"{MODULE}.time.sleep") as mock_sleep,
            patch(f"{MODULE}.gtts.gTTS", fake),
        ):
            fetcher.fetch("食べる", "たべる")

        mock_sleep.assert_called_once_with(0.7)

    def test_negative_delay_clamped_to_zero(self, tmp_path):
        """Negative delay clamps to 0.0 (time.sleep would otherwise raise)."""
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=-1)
        assert fetcher._delay == 0.0

    def test_nan_delay_clamped_to_zero(self, tmp_path):
        """NaN delay clamps to 0.0."""
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=float("nan"))
        assert fetcher._delay == 0.0

    def test_failure_emits_debug_log(self, tmp_path, caplog):
        """A synthesis failure emits a debug log so failures are diagnosable."""
        fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
        with (
            caplog.at_level(logging.DEBUG, logger=MODULE),
            patch(f"{MODULE}.gtts.gTTS", side_effect=RuntimeError("boom")),
        ):
            result = fetcher.fetch("食べる", "たべる")

        assert result is None
        assert any(r.levelno == logging.DEBUG for r in caplog.records)


def test_close_is_noop_and_does_not_raise(tmp_path):
    """close() is a documented no-op (gtts is per-call); must not raise."""
    fetcher = GoogleTranslateAudioFetcher(cache_dir=tmp_path, delay=0)
    fetcher.close()  # no exception expected

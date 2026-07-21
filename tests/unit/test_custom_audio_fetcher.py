"""Tests for CustomAudioFetcher + the shared download_audio_to_cache plumbing."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import requests

from anki_miner.services.custom_audio_fetcher import (
    CustomAudioFetcher,
    _substitute_custom_url,
    custom_audio_slug,
)
from anki_miner.services.expression_audio_fetcher import (
    audio_extension_for_media_type,
    download_audio_to_cache,
)

# Minimal valid ID3v2-tagged MP3 body.
_VALID_MP3 = b"ID3" + b"\x00" * 7 + b"\xff\xfb\x90\x00" + b"\x00" * 100
_OGG_OPUS = b"OggS" + b"\x00" * 60  # not mp3-sniffable; needs a content-type


def _audio_response(content: bytes = _VALID_MP3, content_type: str | None = None, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.url = "http://localhost:5050/audio"
    resp.headers = {"Content-Type": content_type} if content_type is not None else {}
    resp.iter_content.side_effect = lambda chunk_size=8192: iter([content])
    return resp


def _json_response(payload: object, status: int = 200, url: str = "http://localhost:5050/list") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.url = url
    resp.json.return_value = payload
    return resp


def test_audio_fetch_url_secret_never_logged(tmp_path, caplog):
    direct_url = "https://direct-user:direct-pass@example.test/audio.mp3?token=direct#fragment"
    json_url = "https://json-user:json-pass@example.test/list/word?token=json#fragment"

    direct_session = MagicMock()
    direct_session.get.side_effect = requests.ConnectionError("down")
    fetcher = CustomAudioFetcher(
        url_template=json_url,
        kind="custom_json",
        cache_dir=tmp_path / "cache",
        file_prefix="custom_json1",
        delay=0,
    )
    fetcher._session = MagicMock()
    fetcher._session.get.side_effect = requests.ConnectionError("down")

    with caplog.at_level(logging.DEBUG):
        assert download_audio_to_cache(direct_session, direct_url, tmp_path, "stem") is None
        assert fetcher.fetch("word", "reading") is None

    assert "https://example.test/audio.mp3" in caplog.text
    assert "https://example.test/list/word" in caplog.text
    for secret in ("direct-user", "direct-pass", "json-user", "json-pass", "token=", "#fragment"):
        assert secret not in caplog.text


# ---------------------------------------------------------------------------
# _substitute_custom_url (ported _getCustomUrl)
# ---------------------------------------------------------------------------


class TestSubstituteCustomUrl:
    def test_substitutes_term_and_reading(self):
        out = _substitute_custom_url("http://h/?t={term}&r={reading}", "食べる", "たべる", "ja")
        assert out == "http://h/?t=食べる&r=たべる"

    def test_substitutes_language(self):
        assert _substitute_custom_url("http://h/{language}", "x", "y", "ja") == "http://h/ja"

    def test_unknown_placeholder_left_intact(self):
        # Matches upstream: an unrecognized {name} is preserved verbatim.
        assert _substitute_custom_url("http://h/{bogus}?t={term}", "w", "r", "ja") == "http://h/{bogus}?t=w"


class TestCustomAudioSlug:
    def test_stable_and_short(self):
        s1 = custom_audio_slug("http://localhost:5050/?t={term}")
        s2 = custom_audio_slug("http://localhost:5050/?t={term}")
        assert s1 == s2
        assert 0 < len(s1) <= 10
        assert s1.isalnum()

    def test_distinct_urls_distinct_slugs(self):
        assert custom_audio_slug("http://a/") != custom_audio_slug("http://b/")


# ---------------------------------------------------------------------------
# audio_extension_for_media_type (ported media-util map)
# ---------------------------------------------------------------------------


class TestAudioExtensionForMediaType:
    def test_mpeg(self):
        assert audio_extension_for_media_type("audio/mpeg") == ".mp3"

    def test_opus_and_flac_additions(self):
        assert audio_extension_for_media_type("audio/opus") == ".opus"
        assert audio_extension_for_media_type("audio/flac") == ".flac"
        assert audio_extension_for_media_type("audio/aac") == ".aac"

    def test_charset_suffix_and_case_normalized(self):
        assert audio_extension_for_media_type("audio/MPEG; charset=binary") == ".mp3"

    def test_unknown_and_none(self):
        assert audio_extension_for_media_type("text/html") is None
        assert audio_extension_for_media_type(None) is None
        assert audio_extension_for_media_type("") is None


# ---------------------------------------------------------------------------
# download_audio_to_cache
# ---------------------------------------------------------------------------


class TestDownloadAudioToCache:
    def test_mp3_by_magic_no_content_type(self, tmp_path):
        session = MagicMock()
        session.get.return_value = _audio_response(_VALID_MP3, content_type=None)
        result = download_audio_to_cache(session, "http://h/a", tmp_path, "src_word_reading")
        assert result is not None
        assert result.name == "src_word_reading.mp3"
        assert result.read_bytes() == _VALID_MP3

    def test_extension_from_content_type(self, tmp_path):
        session = MagicMock()
        session.get.return_value = _audio_response(_OGG_OPUS, content_type="audio/opus")
        result = download_audio_to_cache(session, "http://h/a", tmp_path, "stem")
        assert result is not None
        assert result.name == "stem.opus"

    def test_non_audio_rejected(self, tmp_path):
        counts = {"non_audio": 0, "http_status": 0, "connection": 0, "timeout": 0, "ssl": 0}
        session = MagicMock()
        session.get.return_value = _audio_response(b"<html>error</html>", content_type="text/html")
        result = download_audio_to_cache(session, "http://h/a", tmp_path, "stem", failure_counts=counts)
        assert result is None
        assert counts["non_audio"] == 1

    def test_non_200_status(self, tmp_path):
        counts = {"non_audio": 0, "http_status": 0, "connection": 0, "timeout": 0, "ssl": 0}
        session = MagicMock()
        session.get.return_value = _audio_response(_VALID_MP3, status=404)
        assert download_audio_to_cache(session, "http://h/a", tmp_path, "s", failure_counts=counts) is None
        assert counts["http_status"] == 1

    def test_empty_body_transient(self, tmp_path):
        counts = {"non_audio": 0, "http_status": 0, "connection": 0, "timeout": 0, "ssl": 0}
        session = MagicMock()
        session.get.return_value = _audio_response(b"", content_type="audio/mpeg")
        assert download_audio_to_cache(session, "http://h/a", tmp_path, "s", failure_counts=counts) is None
        assert counts["connection"] == 1

    def test_network_exception_never_raises(self, tmp_path):
        counts = {"non_audio": 0, "http_status": 0, "connection": 0, "timeout": 0, "ssl": 0}
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("boom")
        assert download_audio_to_cache(session, "http://h/a", tmp_path, "s", failure_counts=counts) is None
        assert counts["connection"] == 1

    def test_no_part_file_left_behind(self, tmp_path):
        session = MagicMock()
        session.get.return_value = _audio_response(_VALID_MP3)
        download_audio_to_cache(session, "http://h/a", tmp_path, "stem")
        assert list(tmp_path.glob("*.part")) == []


# ---------------------------------------------------------------------------
# CustomAudioFetcher — custom (direct URL)
# ---------------------------------------------------------------------------


class TestCustomAudioFetcherDirect:
    def _fetcher(self, tmp_path, kind="custom", template="http://h/?t={term}&r={reading}"):
        f = CustomAudioFetcher(
            url_template=template,
            kind=kind,
            cache_dir=tmp_path / "cache",
            file_prefix="custom_abc",
            delay=0,
        )
        f._session = MagicMock()
        return f

    def test_empty_reading_skips(self, tmp_path):
        f = self._fetcher(tmp_path)
        assert f.fetch("食べる", "") is None
        assert f.fetch("", "たべる") is None
        f._session.get.assert_not_called()

    def test_success_downloads_and_names_by_prefix(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.return_value = _audio_response(_VALID_MP3)
        result = f.fetch("食べる", "たべる")
        assert result is not None
        assert result.name == "custom_abc_食べる_たべる.mp3"
        assert result.exists()

    def test_url_template_substituted(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.return_value = _audio_response(_VALID_MP3)
        f.fetch("食べる", "たべる")
        called_url = f._session.get.call_args[0][0]
        assert called_url == "http://h/?t=食べる&r=たべる"

    def test_cache_hit_skips_network(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.return_value = _audio_response(_VALID_MP3)
        first = f.fetch("食べる", "たべる")
        assert first is not None
        f._session.get.reset_mock()
        second = f.fetch("食べる", "たべる")
        assert second == first
        f._session.get.assert_not_called()

    def test_non_audio_returns_none(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.return_value = _audio_response(b"<html>", content_type="text/html")
        assert f.fetch("食べる", "たべる") is None

    def test_never_raises_on_network_error(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.side_effect = requests.ConnectionError("down")
        assert f.fetch("食べる", "たべる") is None

    def test_cancelled_short_circuits(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.return_value = _audio_response(_VALID_MP3)
        assert f.fetch("食べる", "たべる", cancelled_check=lambda: True) is None
        f._session.get.assert_not_called()


# ---------------------------------------------------------------------------
# CustomAudioFetcher — custom_json (audioSourceList)
# ---------------------------------------------------------------------------


class TestCustomAudioFetcherJson:
    def _fetcher(self, tmp_path, template="http://h/list?t={term}"):
        f = CustomAudioFetcher(
            url_template=template,
            kind="custom_json",
            cache_dir=tmp_path / "cache",
            file_prefix="custom_json1",
            delay=0,
        )
        f._session = MagicMock()
        return f

    def test_valid_list_downloads_first_source(self, tmp_path):
        f = self._fetcher(tmp_path)
        payload = {
            "type": "audioSourceList",
            "audioSources": [
                {"name": "a", "url": "http://h/media/a.mp3"},
                {"name": "b", "url": "http://h/media/b.mp3"},
            ],
        }
        f._session.get.side_effect = [
            _json_response(payload),
            _audio_response(_VALID_MP3),
        ]
        result = f.fetch("食べる", "たべる")
        assert result is not None
        assert result.name == "custom_json1_食べる_たべる.mp3"
        # first GET = the JSON list, second GET = the first audio source URL
        assert f._session.get.call_args_list[0][0][0] == "http://h/list?t=食べる"
        assert f._session.get.call_args_list[1][0][0] == "http://h/media/a.mp3"

    def test_relative_urls_normalized_against_endpoint(self, tmp_path):
        f = self._fetcher(tmp_path)
        payload = {"type": "audioSourceList", "audioSources": [{"url": "/media/rel.mp3"}]}
        f._session.get.side_effect = [
            _json_response(payload, url="http://localhost:5050/list?t=x"),
            _audio_response(_VALID_MP3),
        ]
        f.fetch("食べる", "たべる")
        assert f._session.get.call_args_list[1][0][0] == "http://localhost:5050/media/rel.mp3"

    def test_first_source_miss_falls_to_second(self, tmp_path):
        f = self._fetcher(tmp_path)
        payload = {
            "type": "audioSourceList",
            "audioSources": [{"url": "http://h/a.mp3"}, {"url": "http://h/b.mp3"}],
        }
        f._session.get.side_effect = [
            _json_response(payload),
            _audio_response(b"<html>", content_type="text/html"),  # first is not audio
            _audio_response(_VALID_MP3),  # second succeeds
        ]
        result = f.fetch("食べる", "たべる")
        assert result is not None
        assert f._session.get.call_args_list[2][0][0] == "http://h/b.mp3"

    def test_malformed_source_url_skipped_not_fatal(self, tmp_path):
        """A malformed audioSources URL is skipped (never raises); good URLs remain."""
        f = self._fetcher(tmp_path)
        payload = {
            "type": "audioSourceList",
            "audioSources": [
                {"url": "http://[bad"},  # urljoin → ValueError (Invalid IPv6 URL)
                {"url": "http://h/media/ok.mp3"},
            ],
        }
        f._session.get.return_value = _json_response(payload)
        urls = f._resolve_json_sources("http://h/list")
        assert urls == ["http://h/media/ok.mp3"]

    def test_wrong_type_returns_none(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.return_value = _json_response({"type": "somethingElse", "audioSources": []})
        assert f.fetch("食べる", "たべる") is None

    def test_missing_audiosources_returns_none(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.return_value = _json_response({"type": "audioSourceList"})
        assert f.fetch("食べる", "たべる") is None

    def test_malformed_json_never_raises(self, tmp_path):
        f = self._fetcher(tmp_path)
        bad = _json_response(None)
        bad.json.side_effect = json.JSONDecodeError("bad", "", 0)
        f._session.get.return_value = bad
        assert f.fetch("食べる", "たべる") is None

    def test_non_200_json_returns_none(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.return_value = _json_response({}, status=500)
        assert f.fetch("食べる", "たべる") is None

    def test_stats_and_close(self, tmp_path):
        f = self._fetcher(tmp_path)
        assert set(f.stats()) >= {"non_audio", "http_status", "connection"}
        f.close()  # must not raise

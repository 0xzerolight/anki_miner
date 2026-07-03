"""Tests for the JPod101 dictionary / Jisho HTML-scrape audio fetchers."""

from __future__ import annotations

from unittest.mock import MagicMock

import requests

from anki_miner.services.scrape_audio_fetchers import (
    JishoScrapeFetcher,
    JPod101DictionaryScrapeFetcher,
    _extract_jisho_audio_urls,
    _extract_jpod101_audio_urls,
)

_VALID_MP3 = b"ID3" + b"\x00" * 7 + b"\xff\xfb\x90\x00" + b"\x00" * 100


def _jpod101_row(kana: str, src: str) -> str:
    return (
        f'<div class="dc-result-row"><span class="dc-vocab_kana">{kana}</span>'
        f'<audio><source src="{src}"></audio></div>'
    )


def _html(body: str) -> str:
    return f"<html><body>{body}</body></html>"


def _audio_response(
    content: bytes = _VALID_MP3, content_type: str | None = "audio/mpeg", status: int = 200
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.url = "https://cdn.example.com/a.mp3"
    resp.headers = {"Content-Type": content_type} if content_type is not None else {}
    resp.iter_content.side_effect = lambda chunk_size=8192: iter([content])
    return resp


def _page_response(text: str, url: str, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.url = url
    return resp


# ---------------------------------------------------------------------------
# _extract_jpod101_audio_urls  (+ row reading validation)
# ---------------------------------------------------------------------------

_BASE = "https://www.japanesepod101.com/x"


class TestExtractJpod101:
    def test_hit_returns_normalized_url(self):
        html = _html(_jpod101_row("たべる", "/media/taberu.mp3"))
        urls = _extract_jpod101_audio_urls(html, _BASE, "食べる", "たべる")
        assert urls == ["https://www.japanesepod101.com/media/taberu.mp3"]

    def test_homograph_reading_filtered_out(self):
        # term 辛い requested as つらい; the row is the からい recording → rejected.
        html = _html(_jpod101_row("からい", "https://cdn/karai.mp3"))
        assert _extract_jpod101_audio_urls(html, _BASE, "辛い", "つらい") == []

    def test_correct_reading_selected_among_homographs(self):
        html = _html(_jpod101_row("からい", "https://cdn/karai.mp3") + _jpod101_row("つらい", "https://cdn/tsurai.mp3"))
        urls = _extract_jpod101_audio_urls(html, _BASE, "辛い", "つらい")
        assert urls == ["https://cdn/tsurai.mp3"]

    def test_kana_word_skips_reading_check(self):
        # reading == term (kana word) → the row kana need not match.
        html = _html(_jpod101_row("べつのかな", "https://cdn/x.mp3"))
        urls = _extract_jpod101_audio_urls(html, _BASE, "たべる", "たべる")
        assert urls == ["https://cdn/x.mp3"]

    def test_whitespace_in_row_kana_is_stripped(self):
        html = _html(_jpod101_row("\n  たべる  \n", "https://cdn/x.mp3"))
        urls = _extract_jpod101_audio_urls(html, _BASE, "食べる", "たべる")
        assert urls == ["https://cdn/x.mp3"]

    def test_row_without_kana_rejected(self):
        html = _html('<div class="dc-result-row"><audio><source src="https://cdn/x.mp3"></audio></div>')
        assert _extract_jpod101_audio_urls(html, _BASE, "食べる", "たべる") == []

    def test_row_without_audio_skipped(self):
        html = _html('<div class="dc-result-row"><span class="dc-vocab_kana">たべる</span></div>')
        assert _extract_jpod101_audio_urls(html, _BASE, "食べる", "たべる") == []

    def test_no_rows_empty(self):
        assert _extract_jpod101_audio_urls(_html("<p>nothing</p>"), _BASE, "食べる", "たべる") == []

    def test_empty_string_never_raises(self):
        assert _extract_jpod101_audio_urls("", _BASE, "食べる", "たべる") == []


# ---------------------------------------------------------------------------
# _extract_jisho_audio_urls
# ---------------------------------------------------------------------------


class TestExtractJisho:
    def test_hit_protocol_relative_normalized(self):
        html = _html('<audio id="audio_辛い:つらい"><source src="//cdn.example.com/tsurai.mp3"></audio>')
        urls = _extract_jisho_audio_urls(html, "https://jisho.org/search/x", "辛い", "つらい")
        assert urls == ["https://cdn.example.com/tsurai.mp3"]

    def test_missing_element_empty(self):
        html = _html('<audio id="audio_辛い:からい"><source src="//cdn/x.mp3"></audio>')
        assert _extract_jisho_audio_urls(html, "https://jisho.org/search/x", "辛い", "つらい") == []

    def test_element_without_source_empty(self):
        html = _html('<audio id="audio_食べる:たべる"></audio>')
        assert _extract_jisho_audio_urls(html, "https://jisho.org/search/x", "食べる", "たべる") == []

    def test_empty_string_never_raises(self):
        assert _extract_jisho_audio_urls("", "https://jisho.org/search/x", "食べる", "たべる") == []


# ---------------------------------------------------------------------------
# JPod101DictionaryScrapeFetcher
# ---------------------------------------------------------------------------


class TestJPod101DictionaryScrapeFetcher:
    def _fetcher(self, tmp_path):
        f = JPod101DictionaryScrapeFetcher(cache_dir=tmp_path / "cache", delay=0)
        f._session = MagicMock()
        return f

    def test_empty_reading_skips(self, tmp_path):
        f = self._fetcher(tmp_path)
        assert f.fetch("食べる", "") is None
        f._session.post.assert_not_called()

    def test_success_downloads(self, tmp_path):
        f = self._fetcher(tmp_path)
        html = _html(_jpod101_row("たべる", "https://cdn/taberu.mp3"))
        f._session.post.return_value = _page_response(html, "https://www.japanesepod101.com/x")
        f._session.get.return_value = _audio_response()
        result = f.fetch("食べる", "たべる")
        assert result is not None
        assert result.name == "jpod101scrape_食べる_たべる.mp3"
        # download GET was the extracted audio URL
        assert f._session.get.call_args[0][0] == "https://cdn/taberu.mp3"

    def test_post_payload_shape(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.post.return_value = _page_response(_html("<p/>"), "https://x")
        f.fetch("食べる", "たべる")
        data = f._session.post.call_args.kwargs["data"]
        assert data["post"] == "dictionary_reference"
        assert data["match_type"] == "exact"
        assert data["search_query"] == "食べる"

    def test_homograph_miss_returns_none(self, tmp_path):
        f = self._fetcher(tmp_path)
        html = _html(_jpod101_row("からい", "https://cdn/karai.mp3"))
        f._session.post.return_value = _page_response(html, "https://x")
        assert f.fetch("辛い", "つらい") is None
        f._session.get.assert_not_called()  # no valid URL → no download

    def test_cache_hit_skips_network(self, tmp_path):
        f = self._fetcher(tmp_path)
        html = _html(_jpod101_row("たべる", "https://cdn/taberu.mp3"))
        f._session.post.return_value = _page_response(html, "https://x")
        f._session.get.return_value = _audio_response()
        first = f.fetch("食べる", "たべる")
        assert first is not None
        f._session.post.reset_mock()
        f._session.get.reset_mock()
        assert f.fetch("食べる", "たべる") == first
        f._session.post.assert_not_called()

    def test_never_raises_on_network_error(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.post.side_effect = requests.ConnectionError("down")
        assert f.fetch("食べる", "たべる") is None
        assert f._failure_counts["connection"] == 1

    def test_non_200_scrape(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.post.return_value = _page_response("", "https://x", status=503)
        assert f.fetch("食べる", "たべる") is None
        assert f._failure_counts["http_status"] == 1

    def test_stats_and_close(self, tmp_path):
        f = self._fetcher(tmp_path)
        assert isinstance(f.stats(), dict)
        f.close()


# ---------------------------------------------------------------------------
# JishoScrapeFetcher
# ---------------------------------------------------------------------------


class TestJishoScrapeFetcher:
    def _fetcher(self, tmp_path):
        f = JishoScrapeFetcher(cache_dir=tmp_path / "cache", delay=0)
        f._session = MagicMock()
        return f

    def test_success_downloads(self, tmp_path):
        f = self._fetcher(tmp_path)
        html = _html('<audio id="audio_食べる:たべる"><source src="//cdn/taberu.mp3"></audio>')
        # First GET = search page, second GET = audio download.
        f._session.get.side_effect = [
            _page_response(html, "https://jisho.org/search/x"),
            _audio_response(),
        ]
        result = f.fetch("食べる", "たべる")
        assert result is not None
        assert result.name == "jishoscrape_食べる_たべる.mp3"
        assert f._session.get.call_args_list[1][0][0] == "https://cdn/taberu.mp3"

    def test_search_url_encodes_term(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.return_value = _page_response(_html("<p/>"), "https://jisho.org/search/x")
        f.fetch("食べる", "たべる")
        called = f._session.get.call_args_list[0][0][0]
        assert called.startswith("https://jisho.org/search/")
        assert "食べる" not in called  # percent-encoded

    def test_missing_audio_returns_none(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.return_value = _page_response(_html("<p/>"), "https://jisho.org/search/x")
        assert f.fetch("食べる", "たべる") is None

    def test_never_raises_on_network_error(self, tmp_path):
        f = self._fetcher(tmp_path)
        f._session.get.side_effect = requests.Timeout("slow")
        assert f.fetch("食べる", "たべる") is None
        assert f._failure_counts["timeout"] == 1

    def test_empty_reading_skips(self, tmp_path):
        f = self._fetcher(tmp_path)
        assert f.fetch("食べる", "") is None
        f._session.get.assert_not_called()

"""Tests for sentence-level TTS fetchers (reading sources)."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from anki_miner.services.expression_audio_fetcher import FAILURE_KEYS
from anki_miner.services.sentence_tts_fetcher import (
    MAX_TTS_SENTENCE_CHARS,
    PAPAGO_MAKE_ID_URL,
    ChainedSentenceAudioFetcher,
    GoogleSentenceTtsFetcher,
    PapagoSentenceTtsFetcher,
    _sentence_stem,
)

# The shared gtts synthesis leaf lives in the word-fetcher module; the
# sentence Google fetcher delegates to it, so gTTS is patched there.
GTTS_MODULE = "anki_miner.services.google_translate_audio_fetcher"

_VALID_MP3 = b"ID3" + b"\x00" * 7 + b"\xff\xfb\x90\x00" + b"\x00" * 100

_SENTENCE = "今日はいい天気ですね。"


def _gtts_stub(body: bytes):
    """Fake gTTS class writing *body*; records constructor kwargs."""
    calls: list[dict] = []

    class _FakeGTTS:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)

        def write_to_fp(self, fp):
            fp.write(body)

    return _FakeGTTS, calls


class TestSentenceStem:
    def test_nfc_equivalent_sentences_share_one_stem(self):
        # "が" composed vs か + combining dakuten (NFD)
        composed = "が"
        decomposed = "が"
        assert _sentence_stem("google", composed) == _sentence_stem("google", decomposed)

    def test_whitespace_stripped_before_hashing(self):
        assert _sentence_stem("google", _SENTENCE) == _sentence_stem("google", f"  {_SENTENCE}\n")

    def test_provider_in_stem_keeps_filenames_distinct(self):
        assert _sentence_stem("google", _SENTENCE) != _sentence_stem("papago", _SENTENCE)


class TestGoogleSentenceTtsFetcher:
    def test_fetch_success_caches_mp3(self, tmp_path):
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{GTTS_MODULE}.gtts.gTTS", fake):
            result = fetcher.fetch(_SENTENCE)

        assert result is not None
        assert result.exists()
        assert result.name == f"{_sentence_stem('google', _SENTENCE)}.mp3"
        assert result.read_bytes() == _VALID_MP3

    def test_synthesizes_surface_text(self, tmp_path):
        """The full sentence (kanji included) is fed to gTTS, lang='ja'."""
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{GTTS_MODULE}.gtts.gTTS", fake):
            fetcher.fetch(_SENTENCE)

        assert len(calls) == 1
        assert calls[0]["text"] == _SENTENCE
        assert calls[0]["lang"] == "ja"

    def test_cache_hit_skips_second_synthesis(self, tmp_path):
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{GTTS_MODULE}.gtts.gTTS", fake):
            first = fetcher.fetch(_SENTENCE)
        assert first is not None
        assert len(calls) == 1

        with patch(f"{GTTS_MODULE}.gtts.gTTS", side_effect=AssertionError("synth re-run")):
            second = fetcher.fetch(_SENTENCE)

        assert second == first

    def test_empty_sentence_returns_none_no_synthesis(self, tmp_path):
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{GTTS_MODULE}.gtts.gTTS", fake):
            assert fetcher.fetch("") is None
            assert fetcher.fetch("   \n") is None

        assert calls == []
        assert not list(tmp_path.glob("*"))

    def test_over_cap_sentence_returns_none_no_failure_bump(self, tmp_path):
        """Length cap is an input guard: no synthesis, no failure count."""
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        long_sentence = "あ" * (MAX_TTS_SENTENCE_CHARS + 1)
        with patch(f"{GTTS_MODULE}.gtts.gTTS", fake):
            assert fetcher.fetch(long_sentence) is None

        assert calls == []
        assert fetcher.stats() == dict.fromkeys(FAILURE_KEYS, 0)

    def test_at_cap_sentence_is_synthesized(self, tmp_path):
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{GTTS_MODULE}.gtts.gTTS", fake):
            assert fetcher.fetch("あ" * MAX_TTS_SENTENCE_CHARS) is not None
        assert len(calls) == 1

    def test_non_mp3_body_bumps_non_audio_nothing_cached(self, tmp_path):
        fake, _ = _gtts_stub(b"<html>rate limited</html>")
        fetcher = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{GTTS_MODULE}.gtts.gTTS", fake):
            assert fetcher.fetch(_SENTENCE) is None

        assert fetcher.stats()["non_audio"] == 1
        assert not list(tmp_path.glob("*.mp3"))
        assert not list(tmp_path.glob("*.miss"))

    def test_gtts_raising_returns_none_and_buckets(self, tmp_path):
        fetcher = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{GTTS_MODULE}.gtts.gTTS", side_effect=RuntimeError("boom")):
            assert fetcher.fetch(_SENTENCE) is None
        assert fetcher.stats()["connection"] == 1

    def test_cancelled_check_returns_none_no_synthesis(self, tmp_path):
        fake, calls = _gtts_stub(_VALID_MP3)
        fetcher = GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        with patch(f"{GTTS_MODULE}.gtts.gTTS", fake):
            assert fetcher.fetch(_SENTENCE, cancelled_check=lambda: True) is None
        assert calls == []

    def test_close_is_noop(self, tmp_path):
        GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=0).close()

    def test_nan_delay_clamped(self, tmp_path):
        assert GoogleSentenceTtsFetcher(cache_dir=tmp_path, delay=float("nan"))._delay == 0.0


_DEFAULT_JSON = object()


def _papago_responses(make_id_status=200, make_id_json=_DEFAULT_JSON, audio_status=200, audio_body=_VALID_MP3):
    """Build (post_response, get_response) MagicMocks for the two-step flow."""
    post_resp = MagicMock()
    post_resp.status_code = make_id_status
    if isinstance(make_id_json, Exception):
        post_resp.json.side_effect = make_id_json
    else:
        post_resp.json.return_value = {"id": "abc123"} if make_id_json is _DEFAULT_JSON else make_id_json

    get_resp = MagicMock()
    get_resp.status_code = audio_status
    get_resp.headers = {"Content-Type": "audio/mpeg;charset=UTF-8"}
    get_resp.iter_content.return_value = [audio_body]
    return post_resp, get_resp


def _wire_session(fetcher, post_resp, get_resp):
    session = MagicMock()
    session.post.return_value = post_resp
    session.get.return_value = get_resp
    fetcher._session = session
    return session


class TestPapagoSentenceTtsFetcher:
    def test_two_step_success_caches_audio(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        post_resp, get_resp = _papago_responses()
        session = _wire_session(fetcher, post_resp, get_resp)

        result = fetcher.fetch(_SENTENCE)

        assert result is not None
        assert result.exists()
        assert result.name.startswith(_sentence_stem("papago", _SENTENCE))
        assert result.read_bytes() == _VALID_MP3
        # Step 2 hits the id-URL returned by makeID.
        get_url = session.get.call_args[0][0]
        assert get_url == "https://papago.naver.com/api/tts/abc123"

    def test_make_id_post_form_fields_exact(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        post_resp, get_resp = _papago_responses()
        session = _wire_session(fetcher, post_resp, get_resp)

        fetcher.fetch(_SENTENCE)

        assert session.post.call_args[0][0] == PAPAGO_MAKE_ID_URL
        assert session.post.call_args.kwargs["data"] == {
            "alpha": 0,
            "pitch": 0,
            "speaker": "yuri",
            "speed": 0,
            "text": _SENTENCE,
        }
        assert session.post.call_args.kwargs["timeout"] == 10

    def test_papago_headers_installed_on_session(self, tmp_path):
        """Headers live on the Session so the follow-up GET carries them too."""
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        headers = fetcher._session.headers
        assert headers["Accept"] == "application/json"
        assert headers["Content-Type"] == "application/x-www-form-urlencoded; charset=UTF-8"
        assert headers["Origin"] == "https://papago.naver.com"
        assert headers["Referer"] == "https://papago.naver.com/"
        assert "Mozilla" in headers["User-Agent"]
        fetcher.close()

    def test_make_id_non_200_bumps_http_status(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        post_resp, get_resp = _papago_responses(make_id_status=403)
        session = _wire_session(fetcher, post_resp, get_resp)

        assert fetcher.fetch(_SENTENCE) is None
        assert fetcher.stats()["http_status"] == 1
        session.get.assert_not_called()

    def test_malformed_json_bumps_non_audio_no_raise(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        post_resp, get_resp = _papago_responses(make_id_json=ValueError("not json"))
        _wire_session(fetcher, post_resp, get_resp)

        assert fetcher.fetch(_SENTENCE) is None
        assert fetcher.stats()["non_audio"] == 1

    def test_non_dict_json_bumps_non_audio_no_raise(self, tmp_path):
        """Scraped endpoint shape drift (list/str body) must not raise."""
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        for drifted in (["error"], "error", 42, None):
            post_resp, get_resp = _papago_responses(make_id_json=drifted)
            _wire_session(fetcher, post_resp, get_resp)
            assert fetcher.fetch(_SENTENCE) is None
        assert fetcher.stats()["non_audio"] == 4

    def test_missing_or_non_str_id_bumps_non_audio(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        for payload in ({}, {"id": ""}, {"id": 7}, {"error": "x"}):
            post_resp, get_resp = _papago_responses(make_id_json=payload)
            _wire_session(fetcher, post_resp, get_resp)
            assert fetcher.fetch(_SENTENCE) is None
        assert fetcher.stats()["non_audio"] == 4

    def test_session_raising_unexpected_type_never_raises(self, tmp_path):
        """Even a TypeError/AttributeError from the HTTP layer is swallowed."""
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        for exc in (TypeError("drift"), AttributeError("drift"), KeyError("drift")):
            session = MagicMock()
            session.post.side_effect = exc
            fetcher._session = session
            assert fetcher.fetch(_SENTENCE) is None

    def test_audio_get_failure_buckets_flow_through(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        post_resp, get_resp = _papago_responses(audio_status=500)
        _wire_session(fetcher, post_resp, get_resp)

        assert fetcher.fetch(_SENTENCE) is None
        assert fetcher.stats()["http_status"] == 1

    def test_cache_hit_short_circuits_network(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        stem = _sentence_stem("papago", _SENTENCE)
        cached = tmp_path / f"{stem}.mp3"
        cached.write_bytes(_VALID_MP3)
        session = MagicMock()
        fetcher._session = session

        assert fetcher.fetch(_SENTENCE) == cached
        session.post.assert_not_called()
        session.get.assert_not_called()

    def test_cancelled_between_post_and_get_returns_none(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        post_resp, get_resp = _papago_responses()
        session = _wire_session(fetcher, post_resp, get_resp)

        # Fires only after the POST has happened (calls 1-3 are the guards).
        calls = {"n": 0}

        def cancelled():
            calls["n"] += 1
            return session.post.called

        assert fetcher.fetch(_SENTENCE, cancelled_check=cancelled) is None
        session.post.assert_called_once()
        session.get.assert_not_called()

    def test_empty_and_over_cap_sentences_skip_network(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        session = MagicMock()
        fetcher._session = session
        assert fetcher.fetch("") is None
        assert fetcher.fetch("あ" * (MAX_TTS_SENTENCE_CHARS + 1)) is None
        session.post.assert_not_called()

    def test_close_closes_session(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
        session = MagicMock()
        fetcher._session = session
        fetcher.close()
        session.close.assert_called_once()

    def test_delay_applied_before_make_id(self, tmp_path):
        fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0.4)
        post_resp, get_resp = _papago_responses()
        _wire_session(fetcher, post_resp, get_resp)
        with patch("anki_miner.services.sentence_tts_fetcher.time.sleep") as mock_sleep:
            fetcher.fetch(_SENTENCE)
        mock_sleep.assert_called_once_with(0.4)


class _StubFetcher:
    def __init__(self, result: Path | None, stats: dict[str, int] | None = None):
        self._result = result
        self._stats = stats or {}
        self.calls: list[str] = []
        self.closed = False

    def fetch(self, sentence, cancelled_check=None):
        self.calls.append(sentence)
        return self._result

    def stats(self):
        return self._stats

    def close(self):
        self.closed = True


class TestChainedSentenceAudioFetcher:
    def test_first_hit_wins(self, tmp_path):
        hit = tmp_path / "a.mp3"
        hit.touch()
        first = _StubFetcher(hit)
        second = _StubFetcher(hit)
        chain = ChainedSentenceAudioFetcher([first, second])

        assert chain.fetch(_SENTENCE) == hit
        assert first.calls == [_SENTENCE]
        assert second.calls == []

    def test_falls_through_on_none(self, tmp_path):
        hit = tmp_path / "b.mp3"
        hit.touch()
        chain = ChainedSentenceAudioFetcher([_StubFetcher(None), _StubFetcher(hit)])
        assert chain.fetch(_SENTENCE) == hit

    def test_empty_chain_returns_none(self):
        assert ChainedSentenceAudioFetcher([]).fetch(_SENTENCE) is None

    def test_cancelled_between_members_stops_walk(self, tmp_path):
        hit = tmp_path / "c.mp3"
        hit.touch()
        first = _StubFetcher(None)
        second = _StubFetcher(hit)
        cancelled_after_first = {"value": False}

        def check():
            return cancelled_after_first["value"]

        def fetch_and_cancel(sentence, cancelled_check=None):
            cancelled_after_first["value"] = True
            return None

        first.fetch = fetch_and_cancel  # type: ignore[method-assign]
        chain = ChainedSentenceAudioFetcher([first, second])

        assert chain.fetch(_SENTENCE, cancelled_check=check) is None
        assert second.calls == []

    def test_stats_aggregation(self):
        chain = ChainedSentenceAudioFetcher(
            [
                _StubFetcher(None, {"non_audio": 2, "ssl": 1}),
                _StubFetcher(None, {"non_audio": 1, "unknown_key": 9}),
            ]
        )
        stats = chain.stats()
        assert stats["non_audio"] == 3
        assert stats["ssl"] == 1
        assert "unknown_key" not in stats

    def test_close_fans_out(self):
        members = [_StubFetcher(None), _StubFetcher(None)]
        ChainedSentenceAudioFetcher(members).close()
        assert all(m.closed for m in members)

    def test_conforms_to_protocol(self):
        from anki_miner.interfaces import SentenceAudioFetcher

        chain: SentenceAudioFetcher = ChainedSentenceAudioFetcher([])
        assert chain.fetch("") is None


def test_failure_emits_debug_log(tmp_path, caplog):
    fetcher = PapagoSentenceTtsFetcher(cache_dir=tmp_path, delay=0)
    session = MagicMock()
    import requests

    session.post.side_effect = requests.exceptions.ConnectionError("down")
    fetcher._session = session
    with caplog.at_level(logging.DEBUG, logger="anki_miner.services.sentence_tts_fetcher"):
        assert fetcher.fetch(_SENTENCE) is None
    assert any(r.levelno == logging.DEBUG for r in caplog.records)
    assert fetcher.stats()["connection"] == 1

"""F-16: the Wikimedia Commons word-audio fetcher against a scripted session (no network)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import requests

from anki_miner import __version__
from anki_miner.services import wikimedia_audio_fetcher as wma
from anki_miner.services.audio_fetch_common import reset_fetch_outcome_rate_limit
from anki_miner.services.expression_audio_fetcher import MISS_MARKER_TTL_SECONDS
from anki_miner.services.wikimedia_audio_fetcher import (
    MIN_REQUEST_INTERVAL_S,
    USER_AGENT,
    WIKTIONARY_API_URL,
    WikimediaAudioFetcher,
)

ES = ("LL-Q1321 (spa)-", "Es-", "Es-am-lat-")
COMMONS = "https://upload.wikimedia.org/wikipedia/commons"
PERRO_WAV = "LL-Q1321_%28spa%29-Millars-perro.wav"
MP3 = b"ID3" + b"\x00" * 64
NO_PAGE = {"batchcomplete": True}


class _Response:
    """The slice of ``requests.Response`` the fetcher and ``download_audio_to_cache`` read."""

    def __init__(self, status: int = 200, payload: object = None, body: bytes = b"", content_type: str = "audio/mpeg"):
        self.status_code = status
        self._payload = payload
        self._body = body
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload

    def iter_content(self, chunk_size: int = 8192):
        yield self._body

    def close(self) -> None:
        self.closed = True


class _Session:
    """Answers each ``get`` with the next scripted response (or raises it) and records the call."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, object]] = []

    def get(self, url: str, params: object = None, timeout: object = None, stream: bool = False):
        self.calls.append((url, params))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        pass


def _api(*files: tuple[str, str]) -> _Response:
    pages = [{"title": title, "imageinfo": [{"url": url, "mime": "audio/wav"}]} for title, url in files]
    return _Response(payload={"batchcomplete": True, "query": {"pages": pages}})


@pytest.fixture(autouse=True)
def waits(monkeypatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(wma, "_sleep", recorded.append)
    reset_fetch_outcome_rate_limit()
    return recorded


def _fetcher(tmp_path: Path, session: _Session, prefixes: tuple[str, ...] = ES) -> WikimediaAudioFetcher:
    return WikimediaAudioFetcher(tmp_path / "wikimedia", 0.0, prefixes=prefixes, language="es", session=session)


def test_a_spanish_word_downloads_the_lingua_libre_transcode(tmp_path):
    original = f"{COMMONS}/2/2a/{PERRO_WAV}?utm_source=en.wiktionary.org"
    session = _Session(
        _api(
            ("File:It-perro.ogg", f"{COMMONS}/1/1a/It-perro.ogg"), ("File:LL-Q1321 (spa)-Millars-perro.wav", original)
        ),
        _Response(body=MP3),
    )
    fetcher = _fetcher(tmp_path, session)

    path = fetcher.fetch("perro", "")

    assert path == tmp_path / "wikimedia" / "wikimedia_es_perro.mp3"
    assert path.read_bytes() == MP3
    (api_url, params), (audio_url, _) = session.calls
    assert api_url == WIKTIONARY_API_URL
    assert params == {
        "action": "query",
        "titles": "perro",
        "generator": "images",
        "gimlimit": "100",
        "prop": "imageinfo",
        "iiprop": "url|mime",
        "format": "json",
        "formatversion": "2",
    }
    assert audio_url == f"{COMMONS}/transcoded/2/2a/{PERRO_WAV}/{PERRO_WAV}.mp3"
    assert fetcher.has_cached("perro", "") is True


def test_a_cached_recording_costs_no_request(tmp_path):
    cache = tmp_path / "wikimedia"
    cache.mkdir()
    (cache / "wikimedia_es_perro.mp3").write_bytes(MP3)
    session = _Session()

    assert _fetcher(tmp_path, session).fetch("perro", "") == cache / "wikimedia_es_perro.mp3"
    assert session.calls == []


def test_no_matching_file_writes_a_miss_and_is_not_asked_again(tmp_path):
    session = _Session(_api(("File:It-perro.ogg", f"{COMMONS}/1/1a/It-perro.ogg")))
    fetcher = _fetcher(tmp_path, session)

    assert fetcher.fetch("perro", "") is None
    assert (tmp_path / "wikimedia" / "wikimedia_es_perro.miss").exists()
    assert fetcher.fetch("perro", "") is None
    assert len(session.calls) == 1
    assert fetcher.has_cached("perro", "") is False
    assert fetcher.stats()["http_status"] == 0


def test_a_missing_page_is_a_miss_too(tmp_path):
    fetcher = _fetcher(tmp_path, _Session(_Response(payload=NO_PAGE)))

    assert fetcher.fetch("perrx", "") is None
    assert (tmp_path / "wikimedia" / "wikimedia_es_perrx.miss").exists()


def test_an_expired_miss_is_asked_again(tmp_path):
    cache = tmp_path / "wikimedia"
    cache.mkdir()
    miss = cache / "wikimedia_es_perro.miss"
    miss.touch()
    old = time.time() - MISS_MARKER_TTL_SECONDS - 60
    os.utime(miss, (old, old))
    session = _Session(_Response(payload=NO_PAGE))

    assert _fetcher(tmp_path, session).fetch("perro", "") is None
    assert len(session.calls) == 1
    assert miss.stat().st_mtime > old


def test_a_rate_limit_silences_the_rest_of_the_run(tmp_path):
    session = _Session(_Response(status=429))
    fetcher = _fetcher(tmp_path, session)

    assert fetcher.fetch("perro", "") is None
    assert fetcher.fetch("gato", "") is None
    assert len(session.calls) == 1
    assert list((tmp_path / "wikimedia").glob("*.miss")) == []
    assert fetcher.stats()["http_status"] == 1


def test_a_server_error_writes_no_miss_and_is_asked_again(tmp_path):
    session = _Session(_Response(status=503), _Response(payload=NO_PAGE))
    fetcher = _fetcher(tmp_path, session)

    assert fetcher.fetch("perro", "") is None
    assert not (tmp_path / "wikimedia" / "wikimedia_es_perro.miss").exists()
    assert fetcher.stats()["http_status"] == 1
    assert fetcher.fetch("perro", "") is None
    assert len(session.calls) == 2


@pytest.mark.parametrize("payload", [None, {"error": {"code": "badvalue"}}, ["not", "a", "dict"]])
def test_an_unreadable_answer_writes_no_miss(tmp_path, payload):
    fetcher = _fetcher(tmp_path, _Session(_Response(payload=payload)))

    assert fetcher.fetch("perro", "") is None
    assert not (tmp_path / "wikimedia" / "wikimedia_es_perro.miss").exists()
    assert fetcher.stats()["non_audio"] == 1


def test_a_transport_failure_never_raises(tmp_path):
    fetcher = _fetcher(tmp_path, _Session(requests.ConnectionError("network down")))

    assert fetcher.fetch("perro", "") is None
    assert fetcher.stats()["connection"] == 1


def test_a_transport_failure_silences_the_rest_of_the_run(tmp_path):
    # A blocked or hanging Commons: every later call would also time out, and two
    # 10 s timeouts (Korean's two-rung ladder) eat the chain's whole 20 s per-word
    # budget before Google TTS is asked.
    session = _Session(requests.ConnectTimeout("connect timed out"))
    fetcher = _fetcher(tmp_path, session)

    assert fetcher.fetch_candidates([("perro", ""), ("perros", "")]) is None
    assert fetcher.fetch("gato", "") is None
    assert len(session.calls) == 1
    assert list((tmp_path / "wikimedia").glob("*.miss")) == []
    assert fetcher.stats()["timeout"] == 1


def test_requests_are_at_least_a_second_apart(tmp_path, monkeypatch, waits):
    clock = [100.0]

    def _sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(wma, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(wma, "_sleep", _sleep)
    session = _Session(_Response(payload=NO_PAGE), _Response(payload=NO_PAGE))
    fetcher = WikimediaAudioFetcher(tmp_path / "wikimedia", 0.2, prefixes=ES, language="es", session=session)

    fetcher.fetch("perro", "")
    fetcher.fetch("gato", "")

    assert waits == [pytest.approx(MIN_REQUEST_INTERVAL_S)]


def test_without_prefixes_nothing_is_asked(tmp_path):
    session = _Session()
    fetcher = _fetcher(tmp_path, session, prefixes=())

    assert fetcher.fetch("水", "みず") is None
    assert fetcher.has_cached("水", "みず") is False
    assert session.calls == []


def test_a_cancelled_run_asks_nothing(tmp_path):
    session = _Session()

    assert _fetcher(tmp_path, session).fetch("perro", "", cancelled_check=lambda: True) is None
    assert session.calls == []


def test_candidates_stop_at_the_first_recording(tmp_path):
    session = _Session(
        _Response(payload=NO_PAGE),
        _api(("File:Es-perros.ogg", f"{COMMONS}/3/3c/Es-perros.ogg")),
        _Response(body=MP3),
    )
    fetcher = _fetcher(tmp_path, session)

    found = fetcher.fetch_candidates([("perrxs", ""), ("perros", ""), ("perro", "")])

    assert found == tmp_path / "wikimedia" / "wikimedia_es_perros.mp3"
    assert len(session.calls) == 3


def test_the_session_identifies_anki_miner():
    assert USER_AGENT.startswith(f"AnkiMiner/{__version__} (https://github.com/0xzerolight/anki_miner)")
    session = wma._new_session()
    try:
        assert session.headers["User-Agent"] == USER_AGENT
    finally:
        session.close()

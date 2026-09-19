"""EdgeTtsAudioFetcher against a scripted WebSocket (no network)."""

from __future__ import annotations

import logging
import ssl
from pathlib import Path

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

from anki_miner.services import edge_tts_audio_fetcher as edge
from anki_miner.services.audio_fetch_common import reset_fetch_outcome_rate_limit
from anki_miner.services.edge_tts_audio_fetcher import EDGE_CLIENT, EdgeTtsAudioFetcher, sec_ms_gec

MODULE = "anki_miner.services.edge_tts_audio_fetcher"
VOICE = "sl-SI-PetraNeural"
# An MPEG-2 Layer III frame header (what the service streams), then padding.
MP3 = b"\xff\xf3\x64\xc4" + b"\x00" * 1500

TURN_START = "X-RequestId:abc\r\nContent-Type:application/json; charset=utf-8\r\nPath:turn.start\r\n\r\n{}"
RESPONSE = "X-RequestId:abc\r\nContent-Type:application/json; charset=utf-8\r\nPath:response\r\n\r\n{}"
TURN_END = "X-RequestId:abc\r\nContent-Type:application/json; charset=utf-8\r\nPath:turn.end\r\n\r\n{}"


def audio_frame(payload: bytes, *, content_type: bool = True, path: bytes = b"audio") -> bytes:
    headers = b"X-RequestId:abc\r\n"
    if content_type:
        headers += b"Content-Type:audio/mpeg\r\n"
    headers += b"X-StreamId:1\r\nPath:" + path + b"\r\n"
    return len(headers).to_bytes(2, "big") + headers + payload


def good_stream(body: bytes = MP3) -> list[object]:
    half = len(body) // 2
    return [
        TURN_START,
        RESPONSE,
        audio_frame(body[:half]),
        audio_frame(body[half:]),
        audio_frame(b"", content_type=False),
        TURN_END,
    ]


class FakeConnection:
    def __init__(self, frames: list[object]) -> None:
        self._frames = list(frames)
        self.sent: list[str] = []

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> object:
        assert timeout is not None, "every recv must be bounded"
        frame = self._frames.pop(0)
        if isinstance(frame, BaseException):
            raise frame
        return frame


class Connector:
    """Stands in for websockets' ``connect``: records each call, serves one scripted stream per call."""

    def __init__(self, *streams: list[object] | BaseException) -> None:
        self._streams = list(streams)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.connections: list[FakeConnection] = []

    def __call__(self, url: str, **kwargs: object) -> FakeConnection:
        self.calls.append((url, kwargs))
        stream = self._streams.pop(0)
        if isinstance(stream, BaseException):
            raise stream
        connection = FakeConnection(stream)
        self.connections.append(connection)
        return connection


@pytest.fixture
def connector(monkeypatch):
    def install(*streams: list[object] | BaseException) -> Connector:
        fake = Connector(*streams)
        monkeypatch.setattr(f"{MODULE}._ws_connect", fake)
        return fake

    return install


def speak_term(term: str, reading: str) -> str | None:
    return term


def make(tmp_path: Path, **kwargs: object) -> EdgeTtsAudioFetcher:
    kwargs.setdefault("speakable", speak_term)
    return EdgeTtsAudioFetcher(cache_dir=tmp_path / "edgetts", delay=0, voice=VOICE, **kwargs)


def test_sec_ms_gec_known_answers():
    # Vectors from edge-tts 7.2.8's float formula (drm.py), computed independently.
    first = "D2D305CA876E85D58702947E9D169DEFAF871FD79C00F0B016C920DABAADCE60"
    second = "552C6B9784CA86764A854B37A63134348E717FB7EDB27E68CBAA8A676B83F6DD"
    assert sec_ms_gec(1789000000.0) == first
    assert sec_ms_gec(1789000123.9) == first  # same five-minute bucket
    assert sec_ms_gec(1789000299.99) == second
    assert sec_ms_gec(1789000300.0) == second


def test_a_word_is_synthesised_and_cached(tmp_path, connector):
    fake = connector(good_stream())
    path = make(tmp_path).fetch("knjiga", "")

    assert path == tmp_path / "edgetts" / f"edgetts_{VOICE}_knjiga_.mp3"
    assert path.read_bytes() == MP3
    assert len(fake.calls) == 1


def test_the_handshake_carries_the_token_scheme(tmp_path, connector, monkeypatch):
    monkeypatch.setattr(f"{MODULE}.time.time", lambda: 1789000000.0)
    fake = connector(good_stream())
    make(tmp_path).fetch("knjiga", "")

    url, kwargs = fake.calls[0]
    assert url.startswith(edge.EDGE_TTS_ENDPOINT + "?")
    assert f"TrustedClientToken={EDGE_CLIENT.trusted_client_token}" in url
    assert f"Sec-MS-GEC={sec_ms_gec(1789000000.0)}" in url
    assert f"Sec-MS-GEC-Version=1-{EDGE_CLIENT.chromium_version}" in url
    assert "ConnectionId=" in url
    major = EDGE_CLIENT.chromium_version.split(".")[0]
    assert f"Edg/{major}.0.0.0" in str(kwargs["user_agent_header"])
    assert kwargs["origin"] == "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold"
    assert kwargs["open_timeout"] == 10.0


def test_speech_config_then_ssml_with_the_voice_and_escaped_text(tmp_path, connector):
    fake = connector(good_stream())
    make(tmp_path).fetch("a & <b>", "")

    config, ssml = fake.connections[0].sent
    assert "Path:speech.config" in config
    assert '"outputFormat":"audio-24khz-48kbitrate-mono-mp3"' in config
    assert "Path:ssml" in ssml
    assert f"<voice name='{VOICE}'>a &amp; &lt;b&gt;</voice>" in ssml


def test_a_cached_word_opens_no_connection(tmp_path, connector):
    fake = connector()
    cached = tmp_path / "edgetts" / f"edgetts_{VOICE}_knjiga_.mp3"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(MP3)

    assert make(tmp_path).fetch("knjiga", "") == cached
    assert fake.calls == []


def test_speakable_chooses_the_text(tmp_path, connector):
    fake = connector(good_stream())
    make(tmp_path, speakable=lambda term, reading: reading).fetch("knjiga", "KNJIGA")

    assert ">KNJIGA</voice>" in fake.connections[0].sent[1]


def test_nothing_speakable_opens_no_connection(tmp_path, connector):
    fake = connector()
    assert make(tmp_path, speakable=lambda term, reading: None).fetch("knjiga", "") is None
    assert make(tmp_path).fetch("   ", "") is None
    assert fake.calls == []


def test_no_speakable_keeps_the_japanese_kana_gate(tmp_path, connector):
    fake = connector(good_stream())
    fetcher = make(tmp_path, speakable=None)

    assert fetcher.fetch("辛い", "辛い") is None  # a kanji reading is never guessed at
    assert fetcher.fetch("辛い", "つらい") is not None
    assert ">つらい</voice>" in fake.connections[0].sent[1]


def test_cancellation_before_synthesis_writes_nothing(tmp_path, connector):
    fake = connector()
    assert make(tmp_path).fetch("knjiga", "", cancelled_check=lambda: True) is None
    assert fake.calls == []
    edge_dir = tmp_path / "edgetts"
    assert not edge_dir.exists() or not any(edge_dir.iterdir())


def test_fetch_candidates_returns_the_first_hit(tmp_path, connector):
    connector(good_stream())
    path = make(tmp_path, speakable=lambda term, reading: term or None).fetch_candidates([("", ""), ("knjiga", "")])
    assert path is not None and path.name == f"edgetts_{VOICE}_knjiga_.mp3"


def test_the_voice_namespaces_the_media_file(tmp_path, connector):
    connector(good_stream(), good_stream())
    first = make(tmp_path).fetch("knjiga", "")
    other = EdgeTtsAudioFetcher(cache_dir=tmp_path / "edgetts", delay=0, voice="sl-SI-RokNeural", speakable=speak_term)
    second = other.fetch("knjiga", "")
    assert first is not None and second is not None
    assert first.name != second.name
    assert not first.name.startswith("googletts")


def test_has_cached(tmp_path, connector):
    connector(good_stream())
    fetcher = make(tmp_path)
    assert fetcher.has_cached("knjiga", "") is None  # unknown until synthesised; no .miss exists
    assert fetcher.has_cached("   ", "") is False
    fetcher.fetch("knjiga", "")
    assert fetcher.has_cached("knjiga", "") is True


def test_stats_start_at_zero_and_close_is_a_no_op(tmp_path):
    fetcher = make(tmp_path)
    assert set(fetcher.stats()) >= {"ssl", "connection", "timeout", "http_status", "non_audio", "slow"}
    assert not any(fetcher.stats().values())
    fetcher.close()


def test_a_transport_failure_returns_none_and_never_raises(tmp_path, connector):
    connector(OSError("network is unreachable"))
    fetcher = make(tmp_path)
    assert fetcher.fetch("knjiga", "") is None
    assert sum(fetcher.stats().values()) == 1
    assert not (tmp_path / "edgetts" / f"edgetts_{VOICE}_knjiga_.mp3").exists()


@pytest.fixture(autouse=True)
def _fresh_outcome_counters():
    reset_fetch_outcome_rate_limit()
    yield
    reset_fetch_outcome_rate_limit()


def forbidden() -> InvalidStatus:
    return InvalidStatus(Response(403, "Forbidden", Headers()))


def unsupported_voice() -> ConnectionClosedError:
    return ConnectionClosedError(Close(1007, "Unsupported voice xx-XX-NobodyNeural."), None)


FAILURES = {
    # id: (stream or exception at connect, bucket, log reason)
    "token_rejected": (forbidden, "http_status", "http_status"),
    "handshake_timeout": (lambda: TimeoutError("timed out during opening handshake"), "timeout", "transport"),
    "tls": (lambda: ssl.SSLCertVerificationError("certificate verify failed"), "ssl", "transport"),
    "unreachable": (lambda: OSError("Name or service not known"), "connection", "transport"),
    "unexpected": (lambda: RuntimeError("boom"), "connection", "transport"),
    "recv_timeout": (lambda: [TURN_START, TimeoutError()], "timeout", "transport"),
    "unsupported_voice": (lambda: [unsupported_voice()], "connection", "closed"),
    "short_frame": (lambda: [TURN_START, b"\x00"], "non_audio", "short_frame"),
    "header_overrun": (lambda: [TURN_START, b"\x00\xffPath:audio"], "non_audio", "header_overrun"),
    "unexpected_frame": (lambda: [TURN_START, audio_frame(b"x", path=b"turn.start")], "non_audio", "unexpected_frame"),
    "no_audio": (
        lambda: [TURN_START, RESPONSE, audio_frame(b"", content_type=False), TURN_END],
        "non_audio",
        "empty_body",
    ),
    "not_mp3": (lambda: [TURN_START, audio_frame(b"<html>throttled</html>"), TURN_END], "non_audio", "not_mp3"),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_a_failure_is_a_logged_miss_that_leaves_no_file(tmp_path, connector, caplog, case):
    make_stream, bucket, reason = FAILURES[case]
    connector(make_stream())
    fetcher = make(tmp_path)

    with caplog.at_level(logging.DEBUG, logger=MODULE):
        assert fetcher.fetch("knjiga", "") is None

    assert fetcher.stats()[bucket] == 1
    assert sum(fetcher.stats().values()) == 1
    assert "source=edgetts" in caplog.text
    assert f"reason={reason}" in caplog.text
    assert list((tmp_path / "edgetts").iterdir()) == []  # no mp3, no .miss, no staging file


def test_the_log_names_the_status_and_the_close_reason(tmp_path, connector, caplog):
    connector(forbidden(), [unsupported_voice()])
    fetcher = make(tmp_path)

    with caplog.at_level(logging.DEBUG, logger=MODULE):
        fetcher.fetch("knjiga", "")
        fetcher.fetch("miza", "")

    assert "status=403" in caplog.text
    assert "Unsupported voice xx-XX-NobodyNeural." in caplog.text


def test_an_oversized_stream_is_abandoned(tmp_path, connector, monkeypatch):
    monkeypatch.setattr(f"{MODULE}.MAX_AUDIO_BYTES", 100)
    connector([TURN_START, audio_frame(MP3[:80]), audio_frame(MP3[80:160]), TURN_END])
    fetcher = make(tmp_path)

    assert fetcher.fetch("knjiga", "") is None
    assert fetcher.stats()["non_audio"] == 1


def test_memory_error_is_not_swallowed(tmp_path, connector):
    connector(MemoryError())
    with pytest.raises(MemoryError):
        make(tmp_path).fetch("knjiga", "")

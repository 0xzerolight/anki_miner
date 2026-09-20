"""Slovenian word audio: the seam's edgetts leg, against a scripted WebSocket (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services import edge_tts_audio_fetcher as edge
from anki_miner.services.edge_tts_audio_fetcher import EdgeTtsAudioFetcher

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sl"
VOICES = json.loads((FIXTURES / "edge_voices.json").read_text(encoding="utf-8"))["voices"]

# The frame shapes the seam's own fetcher test scripts (seam plan, Task 4).
MP3 = b"\xff\xf3\x64\xc4" + b"\x00" * 1500
TURN_START = "X-RequestId:abc\r\nContent-Type:application/json; charset=utf-8\r\nPath:turn.start\r\n\r\n{}"
RESPONSE = "X-RequestId:abc\r\nContent-Type:application/json; charset=utf-8\r\nPath:response\r\n\r\n{}"
TURN_END = "X-RequestId:abc\r\nContent-Type:application/json; charset=utf-8\r\nPath:turn.end\r\n\r\n{}"


def _audio_frame(payload: bytes, *, content_type: bool = True) -> bytes:
    headers = b"X-RequestId:abc\r\n"
    if content_type:
        headers += b"Content-Type:audio/mpeg\r\n"
    headers += b"X-StreamId:1\r\nPath:audio\r\n"
    return len(headers).to_bytes(2, "big") + headers + payload


def _good_stream(body: bytes = MP3) -> list[object]:
    half = len(body) // 2
    return [
        TURN_START,
        RESPONSE,
        _audio_frame(body[:half]),
        _audio_frame(body[half:]),
        _audio_frame(b"", content_type=False),
        TURN_END,
    ]


class _Connection:
    def __init__(self, frames: list[object]) -> None:
        self._frames = list(frames)
        self.sent: list[str] = []

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> object:
        assert timeout is not None, "every recv must be bounded"
        return self._frames.pop(0)


def test_the_profile_names_a_voice_the_service_really_has():
    """Recorded from the live voice list on 2026-09-20; Rok is the documented alternative."""
    short_names = [voice["ShortName"] for voice in VOICES]
    assert get_profile("sl").audio.edge_voice in short_names
    assert short_names == ["sl-SI-PetraNeural", "sl-SI-RokNeural"]
    assert all(voice["Locale"] == "sl-SI" and voice["Status"] == "GA" for voice in VOICES)
    assert all(voice["SuggestedCodec"] == "audio-24khz-48kbitrate-mono-mp3" for voice in VOICES)


def test_google_has_no_slovenian_voice():
    """Why the default leg is Edge at all (seam D14); the seam's contract test enforces the rule."""
    from gtts.lang import tts_langs

    assert "sl" not in tts_langs()
    assert get_profile("sl").audio.gtts_lang == ""


def test_the_default_chain_is_the_edge_leg():
    config = switch_language(AnkiMinerConfig(), "sl")
    assert [entry.kind for entry in config.expression_audio_chain] == ["edgetts"]


@pytest.fixture
def connection(monkeypatch):
    made: list[_Connection] = []

    def connect(url: str, **kwargs: object) -> _Connection:
        made.append(_Connection(_good_stream()))
        return made[-1]

    monkeypatch.setattr(edge, "_ws_connect", connect)
    return made


def test_a_slovenian_word_is_synthesised_with_the_profile_voice(tmp_path, connection):
    """Through the fetcher's documented _ws_connect seam: the SSML names the profile's voice."""
    audio = get_profile("sl").audio
    fetcher = EdgeTtsAudioFetcher(
        cache_dir=tmp_path / "edgetts", delay=0, voice=audio.edge_voice, speakable=audio.speakable
    )

    path = fetcher.fetch("knjiga", "")

    assert path is not None and path.name == f"edgetts_{audio.edge_voice}_knjiga_.mp3"
    assert path.read_bytes() == MP3
    assert f"<voice name='{audio.edge_voice}'>knjiga</voice>" in connection[0].sent[1]


def test_a_caron_word_reaches_the_service_unescaped(tmp_path, connection):
    """Slovenian letters are ordinary XML text: only &, < and > are escaped by the seam."""
    audio = get_profile("sl").audio
    fetcher = EdgeTtsAudioFetcher(
        cache_dir=tmp_path / "edgetts", delay=0, voice=audio.edge_voice, speakable=audio.speakable
    )

    path = fetcher.fetch("žival", "")

    assert path is not None and path.name == f"edgetts_{audio.edge_voice}_žival_.mp3"
    assert ">žival</voice>" in connection[0].sent[1]

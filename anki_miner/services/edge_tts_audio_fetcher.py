"""Microsoft Edge read-aloud expression-audio fetcher (the ``edgetts`` audio kind).

Synthetic word pronunciation audio for a mining language Google Translate has
no voice for (``AudioDefaults.gtts_lang == ""``), and an optional leg for any
language whose profile names an Edge voice (``AudioDefaults.edge_voice``).

The client is our own, over ``websockets``: the Edge browser's read-aloud
WebSocket, one connection per word — send ``speech.config`` and the SSML, then
collect the binary ``Path:audio`` frames until ``Path:turn.end``. The
``edge-tts`` project (LGPLv3) documents the protocol; none of its code is
vendored or imported.

Contract, shared with :class:`GoogleTranslateAudioFetcher`:

* **Never raises** (``MemoryError`` excepted): the Phase-3 loop has no
  try/except, so every failure returns None and tallies into ``FAILURE_KEYS``.
* **No ``.miss`` markers.** Synthetic speech exists for any word; a failure is
  transient (network, throttling, a rotated token scheme) and is retried on
  the next run.
* **Atomic cache write**, so a killed run never leaves a truncated mp3 that
  would pass the ``st_size > 0`` cache-hit check.
"""

from __future__ import annotations

import hashlib
import json
import logging
import ssl
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import ClientConnection
from websockets.sync.client import connect as _ws_connect
from websockets.typing import Origin

from anki_miner.services.audio_fetch_common import (
    MAX_AUDIO_BYTES,
    first_candidate_hit,
    is_mp3,
    log_fetch_outcome,
    new_failure_counts,
    scrub_url_secrets,
)
from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.file_utils import safe_filename
from anki_miner.utils.text_utils import is_kana_only

logger = logging.getLogger(__name__)

EDGE_TTS_ENDPOINT = "wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1"


@dataclass(frozen=True)
class EdgeClientIdentity:
    """The pair the endpoint checks a caller against."""

    trusted_client_token: str
    #: Full Edge (Chromium) version: the major goes in the User-Agent, the
    #: whole string in ``Sec-MS-GEC-Version``.
    chromium_version: str


#: The Edge read-aloud token scheme, maintained like the yt-dlp pin. When
#: Microsoft rotates it, every word misses with ``reason=http_status status=403``
#: in the log; the fix is the new pair from the edge-tts project's
#: ``constants.py`` (``TRUSTED_CLIENT_TOKEN``, ``CHROMIUM_FULL_VERSION``). A 403
#: on every word means a rotated token or a system clock off by more than five
#: minutes (Sec-MS-GEC is time-bucketed). The endpoint refuses a handshake
#: without the Edge User-Agent, ``Sec-MS-GEC`` or ``Sec-MS-GEC-Version`` (probed
#: 2026-09-19 with edge-tts 7.2.8's values).
EDGE_CLIENT = EdgeClientIdentity(
    trusted_client_token="6A5AA1D4EAFF4E9FB37E23D68491D6F4",
    chromium_version="143.0.3650.75",
)

_ORIGIN = Origin("chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold")
#: Seconds from 1601-01-01 (Windows file time) to 1970-01-01 (Unix time).
_WINDOWS_EPOCH_OFFSET = 11_644_473_600
#: ``Sec-MS-GEC`` changes every five minutes; the endpoint takes the current
#: bucket and its neighbours only (probed: +-300 s pass, +-600 s get 403).
_GEC_BUCKET_SECONDS = 300
_OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
#: Handshake and per-message receive bound — the 10 s the other online fetchers
#: use. The chain's per-word budget bounds the whole walk.
_TIMEOUT_SECONDS = 10.0
_SPEECH_CONFIG = json.dumps(
    {
        "context": {
            "synthesis": {
                "audio": {
                    "metadataoptions": {"sentenceBoundaryEnabled": "false", "wordBoundaryEnabled": "false"},
                    "outputFormat": _OUTPUT_FORMAT,
                }
            }
        }
    },
    separators=(",", ":"),
)


def sec_ms_gec(unix_time: float, token: str = EDGE_CLIENT.trusted_client_token) -> str:
    """The ``Sec-MS-GEC`` value for *unix_time*.

    SHA-256 over the start of the five-minute bucket in Windows file time
    (100-ns ticks since 1601) followed by the trusted-client token, upper hex.
    """
    seconds = int(unix_time) + _WINDOWS_EPOCH_OFFSET
    seconds -= seconds % _GEC_BUCKET_SECONDS
    return hashlib.sha256(f"{seconds * 10_000_000}{token}".encode("ascii")).hexdigest().upper()


def _user_agent() -> str:
    major = EDGE_CLIENT.chromium_version.split(".", 1)[0]
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36 Edg/{major}.0.0.0"
    )


def _js_date() -> str:
    """The JavaScript ``Date.toString()`` shape the Edge client stamps messages with."""
    return time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime())


def _config_message() -> str:
    return (
        f"X-Timestamp:{_js_date()}\r\n"
        "Content-Type:application/json; charset=utf-8\r\n"
        f"Path:speech.config\r\n\r\n{_SPEECH_CONFIG}\r\n"
    )


def _ssml_message(voice: str, text: str) -> str:
    ssml = (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
        f"<voice name='{voice}'>{escape(text)}</voice></speak>"
    )
    # The "Z" after a JavaScript date is not a typo: the Edge client sends it.
    return (
        f"X-RequestId:{uuid.uuid4().hex}\r\n"
        "Content-Type:application/ssml+xml\r\n"
        f"X-Timestamp:{_js_date()}Z\r\n"
        f"Path:ssml\r\n\r\n{ssml}"
    )


class _MalformedStream(Exception):
    """The service answered, but not with an mp3 stream; ``reason`` names how."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _read_audio(connection: ClientConnection) -> bytes:
    """Collect the ``Path:audio`` payloads until ``Path:turn.end``.

    A binary frame is a two-byte big-endian header length, the header lines,
    then the payload; the stream's closing audio frame has no Content-Type and
    no payload. Text frames other than ``turn.end`` are progress and carry no
    audio.
    """
    audio = bytearray()
    while True:
        message = connection.recv(timeout=_TIMEOUT_SECONDS)
        if isinstance(message, str):
            if "Path:turn.end" in message.split("\r\n\r\n", 1)[0].split("\r\n"):
                return bytes(audio)
            continue
        if len(message) < 2:
            raise _MalformedStream("short_frame")
        header_length = int.from_bytes(message[:2], "big")
        if 2 + header_length > len(message):
            raise _MalformedStream("header_overrun")
        headers = bytes(message[2 : 2 + header_length]).split(b"\r\n")
        if b"Path:audio" not in headers:
            raise _MalformedStream("unexpected_frame")
        if b"Content-Type:audio/mpeg" in headers:
            audio += message[2 + header_length :]
            if len(audio) > MAX_AUDIO_BYTES:
                raise _MalformedStream("oversize")


def _classify_failure(exc: Exception) -> tuple[str, str, int | None]:
    """(FAILURE_KEYS bucket, log reason, HTTP status) for what a synthesis raised.

    Most specific first: ``ssl.SSLError`` and ``TimeoutError`` are both
    ``OSError`` subclasses, and a TLS failure is the one worth naming.
    """
    if isinstance(exc, _MalformedStream):
        return "non_audio", exc.reason, None
    if isinstance(exc, InvalidStatus):
        # A rotated token scheme or a refused Sec-MS-GEC: 403 on every word.
        return "http_status", "http_status", exc.response.status_code
    if isinstance(exc, ssl.SSLError):
        return "ssl", "transport", None
    if isinstance(exc, TimeoutError):
        return "timeout", "transport", None
    if isinstance(exc, ConnectionClosed):
        # The service refuses SSML it cannot speak (an unknown voice) by
        # closing with 1007 and the reason, which the error field carries.
        return "connection", "closed", None
    return "connection", "transport", None


class EdgeTtsAudioFetcher:
    """Synthesizes word pronunciation audio with a Microsoft Edge read-aloud voice.

    Successful syntheses are cached as ``.mp3`` files; no negative markers are
    written. Implements the :class:`~anki_miner.interfaces.ExpressionAudioFetcher`
    protocol structurally; ``fetch`` never raises.
    """

    def __init__(
        self,
        cache_dir: Path,
        delay: float = 0.2,
        *,
        voice: str,
        speakable: Callable[[str, str], str | None] | None = None,
    ) -> None:
        """Initialize with cache directory, politeness delay and voice.

        Args:
            cache_dir: Directory for cached mp3s (``audio_cache/edgetts/``).
            delay: Seconds to wait before each synthesis request.
            voice: Short Edge voice name (``AudioDefaults.edge_voice``).
            speakable: The text to synthesise for a ``(mined_form, reading)``
                pair, or None to skip it (``AudioDefaults.speakable``). ``None``
                keeps the Japanese gate: the reading, only when it is kana.
        """
        self._cache_dir = cache_dir
        # NaN must clamp to 0.0 (time.sleep(nan) raises); nan >= 0.0 is False.
        self._delay = delay if delay >= 0.0 else 0.0
        self._voice = voice
        self._speakable = speakable
        self._failure_counts = new_failure_counts()

    def _speakable_text(self, mined_form: str, reading: str) -> str | None:
        """The text to synthesise for this pair, or None when the pair is skipped."""
        if not mined_form.strip():
            return None
        if self._speakable is None:
            return reading if is_kana_only(reading.strip()) else None
        text = self._speakable(mined_form, reading)
        return text if text and text.strip() else None

    def _cache_path(self, mined_form: str, reading: str) -> Path:
        # The stem doubles as the Anki media filename. The voice names the
        # language and the speaker, so no stem collides with another voice's
        # file or with the Google leg's, and no language code is needed here.
        return self._cache_dir / f"{safe_filename(f'edgetts_{self._voice}_{mined_form}_{reading}')}.mp3"

    def fetch(
        self,
        mined_form: str,
        reading: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Synthesize pronunciation audio for a word; a cached mp3 path, or None. Never raises."""
        text = self._speakable_text(mined_form, reading)
        if text is None:
            return None
        if cancelled_check is not None and cancelled_check():
            return None
        mp3_path = self._cache_path(mined_form, reading)
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            if mp3_path.exists() and mp3_path.stat().st_size > 0:
                return mp3_path
            if cancelled_check is not None and cancelled_check():
                return None
            time.sleep(self._delay)
            if cancelled_check is not None and cancelled_check():
                return None
            body = self._synthesize(text)
            if not body:
                self._failure_counts["non_audio"] += 1
                log_fetch_outcome(
                    logger, "edgetts", mined_form, reading, EDGE_TTS_ENDPOINT, bytes_=0, reason="empty_body"
                )
                return None
            if not is_mp3(body):
                self._failure_counts["non_audio"] += 1
                log_fetch_outcome(
                    logger, "edgetts", mined_form, reading, EDGE_TTS_ENDPOINT, bytes_=len(body), reason="not_mp3"
                )
                return None
            with atomic_write_path(mp3_path) as staged:
                staged.write_bytes(body)
            return mp3_path
        # MemoryError is not part of the never-raises contract (the rule
        # service_factory applies to optional sources): a starved interpreter
        # must abort the run, not keep writing cards.
        except MemoryError:
            raise
        # Broad on purpose: websockets, ssl and the socket layer raise many
        # types, and the Phase-3 loop has no try/except by design.
        except Exception as exc:
            bucket, reason, status = _classify_failure(exc)
            self._failure_counts[bucket] += 1
            if isinstance(exc, _MalformedStream):
                log_fetch_outcome(logger, "edgetts", mined_form, reading, EDGE_TTS_ENDPOINT, reason=reason)
            else:
                log_fetch_outcome(
                    logger,
                    "edgetts",
                    mined_form,
                    reading,
                    EDGE_TTS_ENDPOINT,
                    status=status,
                    reason=reason,
                    error=f"{type(exc).__name__}: {scrub_url_secrets(str(exc), EDGE_TTS_ENDPOINT, 'edgetts')}",
                )
            return None

    def _synthesize(self, text: str) -> bytes:
        """One connection, one word: the mp3 bytes the service streamed back."""
        url = (
            f"{EDGE_TTS_ENDPOINT}?TrustedClientToken={EDGE_CLIENT.trusted_client_token}"
            f"&ConnectionId={uuid.uuid4().hex}"
            f"&Sec-MS-GEC={sec_ms_gec(time.time())}"
            f"&Sec-MS-GEC-Version=1-{EDGE_CLIENT.chromium_version}"
        )
        with _ws_connect(
            url,
            origin=_ORIGIN,
            user_agent_header=_user_agent(),
            open_timeout=_TIMEOUT_SECONDS,
            close_timeout=_TIMEOUT_SECONDS,
        ) as connection:
            connection.send(_config_message())
            connection.send(_ssml_message(self._voice, text))
            return _read_audio(connection)

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Try each candidate form, returning the first synthesized hit."""
        return first_candidate_hit(self, candidates, cancelled_check)

    def has_cached(self, mined_form: str, reading: str) -> bool | None:
        """Disk-only: True for a cached mp3, False when nothing is speakable, else None.

        No definitive-miss answer exists: there are no ``.miss`` markers.
        """
        if self._speakable_text(mined_form, reading) is None:
            return False
        try:
            mp3_path = self._cache_path(mined_form, reading)
            if mp3_path.exists() and mp3_path.stat().st_size > 0:
                return True
        except OSError:
            return None
        return None

    def stats(self) -> dict[str, int]:
        """A copy of this run's failure-cause counts (see ``FAILURE_KEYS``)."""
        return dict(self._failure_counts)

    def close(self) -> None:
        """No-op: every word opens and closes its own connection."""

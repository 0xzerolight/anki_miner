"""Shared HTTP / cache / failure toolkit for the audio fetchers.

Extracted (ARC-019) from ``expression_audio_fetcher`` so the sibling fetchers
(JPod101, Google Translate TTS, custom, local-audio-yomichan packs, sentence
TTS) can share the browser-session, on-disk cache, atomic-download and
failure-cause-classification plumbing without importing through
``expression_audio_fetcher``.  ``expression_audio_fetcher`` re-exports the old
underscore-prefixed names for backward compatibility.
"""

import contextlib
import logging
import os
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit, urlunsplit

import requests

from anki_miner.models import TokenizedWord
from anki_miner.services.subtitle_parser import _differs_by_okurigana_only
from anki_miner.utils import has_katakana, hiragana_to_katakana

if TYPE_CHECKING:
    from anki_miner.interfaces.expression_audio import ExpressionAudioFetcher

logger = logging.getLogger(__name__)


def expression_audio_candidates(word: TokenizedWord) -> list[tuple[str, str]]:
    """Ordered ``(kanji, kana)`` query pairs for the JPod101 audio retry ladder.

    Two failure modes the single-shot query missed:

    * **Katakana loanwords.** JPod101 indexes loanword audio under the katakana
      reading, but ``expression_reading`` is folded to hiragana for card
      display (チップ→ちっぷ → miss).  Each query whose kanji form contains
      katakana gets a katakana-reading variant (チップ→チップ → hit).
    * **Surface-mined fallback.** A same-kanji, okurigana-only UniDic lemma is a
      safe canonical alternate. Surface-mined words fall back to that lemma with
      the lemma's OWN reading (探す/さがす, not the surface 探し/さがし).

    hiragana↔katakana is lossless and loanwords are unambiguous, so the katakana
    variant carries no homograph risk (Issue #73). Different-kanji lemmas are
    excluded because UniDic canonicalization can name another homograph. Empty
    readings are dropped and duplicates are collapsed, so a verb whose
    ``mined_form == lemma`` issues no redundant request.

    Lives here rather than beside its mining caller (``AudioStage``) because
    Card Backfill builds the same ladder for an existing note, and ``services/``
    must not import ``orchestration/``.
    """
    pairs: list[tuple[str, str]] = [(word.mined_form, word.expression_reading)]
    if word.lemma and word.lemma != word.mined_form and _differs_by_okurigana_only(word.mined_form, word.lemma):
        pairs.append((word.lemma, word.lemma_reading))

    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(kanji: str, kana: str) -> None:
        if not kanji or not kana:
            return
        pair = (kanji, kana)
        if pair not in seen:
            seen.add(pair)
            candidates.append(pair)

    for kanji, kana in pairs:
        _add(kanji, kana)
        if has_katakana(kanji):
            _add(kanji, hiragana_to_katakana(kana))
    return candidates


# Valid words 301-redirect to the CloudFront CDN (cdn.innovativelanguage.com),
# which returns HTTP 403 + an HTML error page to the default
# "python-requests/x.y" User-Agent. A browser-style UA is required — the same a
# browser or Yomitan sends — otherwise EVERY present word fails the is_mp3
# check and falls through to a synthetic fallback. (Genuinely-absent words are
# served the placeholder mp3 by the PHP endpoint directly, with no CDN redirect,
# so they still produce a correct .miss even with the default UA — which is why
# the symptom was "0 hits, a few misses, everything synthesized".)
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Real word audio is ~10–100 KB. 5 MB is a generous upper bound; anything
# larger is almost certainly an error page or CDN redirect body.
MAX_AUDIO_BYTES = 5 * 1024 * 1024

_AUDIO_CACHE_INDEX_MAX_DIRS = 16
_AUDIO_CACHE_INDEXES: OrderedDict[Path, tuple[tuple[int, int, int, int], dict[str, Path]]] = OrderedDict()
_AUDIO_CACHE_INDEX_LOCK = threading.Lock()


def _index_audio_path(index: dict[str, Path], path: Path) -> None:
    if path.name.endswith(".part") or not path.is_file():
        return
    for dot_index, char in enumerate(path.name):
        if char == ".":
            index.setdefault(path.name[:dot_index], path)


def record_cached_path(cache_dir: Path, path: Path) -> None:
    """Add a newly-written cache file without rescanning the growing directory."""
    try:
        stat = cache_dir.stat()
    except OSError:
        return
    signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns)
    with _AUDIO_CACHE_INDEX_LOCK:
        cached = _AUDIO_CACHE_INDEXES.get(cache_dir)
        if cached is None:
            return
        _index_audio_path(cached[1], path)
        _AUDIO_CACHE_INDEXES[cache_dir] = (signature, cached[1])
        _AUDIO_CACHE_INDEXES.move_to_end(cache_dir)


def redact_url_for_log(url: str) -> str:
    """Keep scheme, host, port, and path; fail closed when userinfo exists."""
    try:
        parts = urlsplit(url)
        if parts.username is not None or "@" in unquote(parts.netloc):
            return "<redacted-url>"
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return "<redacted-url>"
    if not parts.scheme or hostname is None:
        return "<redacted-url>"
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def is_mp3(body: bytes) -> bool:
    """Return True if *body* looks like MP3 audio.

    Accepts either an ID3v2 tag header (b"ID3") or a raw MPEG frame-sync
    sequence (first byte 0xFF, top 3 bits of second byte all set).
    """
    if len(body) < 2:
        return False
    if body[:3] == b"ID3":
        return True
    # MPEG frame sync: 0xFF followed by a byte whose top 3 bits are all 1.
    return bool(body[0] == 0xFF and (body[1] & 0xE0) == 0xE0)


# Ported from Yomitan ext/js/media/media-util.js
# (getFileExtensionFromAudioMediaType), upstream commit
# e2ed450c2f11a591922822e77f008e70a87daf0c. Maps a response Content-Type to the
# file extension used for the cached Anki media filename. The two entries marked
# below are additions beyond upstream: local-audio-yomichan serves opus/flac and
# some servers label FLAC as audio/x-flac, neither of which upstream lists.
AUDIO_MEDIA_TYPE_EXTENSIONS: dict[str, str] = {
    "audio/aac": ".aac",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".mp4",
    "audio/ogg": ".ogg",
    "audio/vorbis": ".ogg",
    "application/ogg": ".ogg",
    "audio/opus": ".opus",  # addition (l-a-y opus); not in upstream
    "audio/vnd.wav": ".wav",
    "audio/wave": ".wav",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/x-pn-wav": ".wav",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",  # addition (common FLAC alias); not in upstream
    "audio/webm": ".webm",
}


def audio_extension_for_media_type(media_type: str | None) -> str | None:
    """Return the file extension (incl. dot) for an audio Content-Type, or None.

    Any charset/parameter suffix (``; charset=...``) and case are normalized off
    before the lookup so ``audio/MPEG; q=1`` resolves like ``audio/mpeg``.
    """
    if not media_type:
        return None
    key = media_type.split(";", 1)[0].strip().lower()
    return AUDIO_MEDIA_TYPE_EXTENSIONS.get(key)


def new_browser_session() -> "requests.Session":
    """Return a fresh ``requests.Session`` presenting the browser User-Agent.

    Shared by every online audio fetcher: the CDN behind JPod101's 301 redirect
    (and, defensively, other endpoints) 403s the default ``python-requests`` UA
    — see ``_BROWSER_USER_AGENT``.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": _BROWSER_USER_AGENT})
    return session


def find_cached_by_stem(cache_dir: Path, stem: str) -> Path | None:
    """Return a cached audio file whose name is ``<stem>.<ext>``, or None.

    Extension varies by source (mp3/opus/flac/…), so match any suffix. A bounded
    per-directory index preserves the first ``iterdir`` match for arbitrary
    stems, including glob metacharacters. Skips ``.part`` staging files left by
    a crashed prior download. A missing or unreadable directory yields None.
    """
    try:
        stat = cache_dir.stat()
        signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns)
        with _AUDIO_CACHE_INDEX_LOCK:
            cached = _AUDIO_CACHE_INDEXES.get(cache_dir)
            if cached is not None and cached[0] == signature:
                _AUDIO_CACHE_INDEXES.move_to_end(cache_dir)
                return cached[1].get(stem)

            index: dict[str, Path] = {}
            for path in cache_dir.iterdir():
                _index_audio_path(index, path)
            _AUDIO_CACHE_INDEXES[cache_dir] = (signature, index)
            _AUDIO_CACHE_INDEXES.move_to_end(cache_dir)
            if len(_AUDIO_CACHE_INDEXES) > _AUDIO_CACHE_INDEX_MAX_DIRS:
                _AUDIO_CACHE_INDEXES.popitem(last=False)
            return index.get(stem)
    except OSError:
        return None


# Per-run audio failure-cause buckets (Issue: audio-failure-cause-classification).
# Ported concept from Yomitan's Backend._getAudioDownloadError
# (ext/js/background/backend.js, upstream commit
# e2ed450c2f11a591922822e77f008e70a87daf0c), which maps error classes to distinct
# diagnoses — notably the historical expired-server-certificate incident. Here the
# never-raises fetchers tally why each transient failure happened so the pipeline
# can name the dominant cause instead of reporting an undiagnosable "X/Y available".
FAILURE_KEYS = ("ssl", "connection", "timeout", "http_status", "non_audio")


def new_failure_counts() -> dict[str, int]:
    """Return a fresh, zeroed failure-cause counter for one run."""
    return dict.fromkeys(FAILURE_KEYS, 0)


def classify_request_exception(exc: BaseException) -> str:
    """Map a raised request/OS exception to a failure-cause bucket.

    Checks are ordered most-specific first: ``SSLError`` subclasses
    ``ConnectionError`` and ``ConnectTimeout`` subclasses both ``Timeout`` and
    ``ConnectionError``, so a naive order would misfile the expired-certificate
    case (the whole point of this classification) as a plain connection error.
    Anything else (generic ``RequestException``, ``OSError``) falls to
    ``connection`` — a transport-family failure retried next run.
    """
    if isinstance(exc, requests.exceptions.SSLError):
        return "ssl"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    return "connection"


def download_audio_to_cache(
    session: "requests.Session",
    url: str,
    cache_dir: Path,
    stem: str,
    *,
    timeout: int = 10,
    failure_counts: dict[str, int] | None = None,
    cancelled_check: Callable[[], bool] | None = None,
) -> Path | None:
    """GET *url*, validate it is audio, and atomically cache it as ``<stem><ext>``.

    Shared leaf for the custom and scrape fetchers (they reuse JPod101's
    Session/UA/size-cap plumbing). The extension is chosen from the response
    Content-Type (``audio_extension_for_media_type``), falling back to ``.mp3``
    when the body sniffs as MP3 (``is_mp3``) — this covers l-a-y's opus/flac/aac
    as well as servers that omit or mislabel the type on an MP3.

    Never raises: transient failures (non-200, oversized/empty/non-audio body,
    network/OS error) tally into *failure_counts* (if given, keyed by
    ``FAILURE_KEYS``) and return None. Unlike JPod101 no ``.miss`` marker is ever
    written — custom/scrape server contents change, so a miss now may be a hit
    later. Successful downloads ARE cached (Anki-media-unique ``stem`` supplied
    by the caller). The write is atomic (unique ``.part`` temp + ``os.replace``)
    so a killed process cannot leave a truncated file that passes a later
    cache-hit check.
    """

    def _bump(key: str) -> None:
        if failure_counts is not None:
            failure_counts[key] += 1

    try:
        response = session.get(url, timeout=timeout, stream=True)
        try:
            if response.status_code != 200:
                _bump("http_status")
                return None

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=8192):
                if cancelled_check is not None and cancelled_check():
                    return None
                total += len(chunk)
                if total > MAX_AUDIO_BYTES:
                    _bump("non_audio")
                    return None
                chunks.append(chunk)
            body = b"".join(chunks)

            if not body:
                _bump("connection")
                return None

            ext = audio_extension_for_media_type(response.headers.get("Content-Type"))
            if ext is None and is_mp3(body):
                ext = ".mp3"
            if ext == ".mp3" and not is_mp3(body):
                _bump("non_audio")
                return None
            if ext is None:
                # Not recognizable audio (HTML error page, unknown type) —
                # transient; retried next run since no marker is written.
                _bump("non_audio")
                return None

            cache_dir.mkdir(parents=True, exist_ok=True)
            dest = cache_dir / f"{stem}{ext}"
            with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".part", delete=False) as tmp_fd:
                tmp_name = tmp_fd.name
                try:
                    tmp_fd.write(body)
                except OSError:
                    with contextlib.suppress(OSError):
                        Path(tmp_name).unlink()
                    raise
            try:
                os.replace(tmp_name, dest)
            except OSError:
                with contextlib.suppress(OSError):
                    Path(tmp_name).unlink()
                raise
            record_cached_path(cache_dir, dest)
            return dest
        finally:
            response.close()
    except (requests.RequestException, OSError) as exc:
        _bump(classify_request_exception(exc))
        logger.debug(
            "audio download failed (%s): %s: %s",
            redact_url_for_log(url),
            type(exc).__name__,
            redact_url_for_log(str(exc)),
        )
        return None


def aggregate_failure_stats(fetchers: "Sequence[object]") -> dict[str, int]:
    """Aggregate per-run failure-cause counts across member fetchers.

    Shared by the expression- and sentence-audio chains. ``stats()`` is
    optional/duck-typed: members without it are skipped, and a member raising
    is suppressed so diagnostics never break a run. Unknown keys from a member
    are ignored; missing keys default to zero.
    """
    totals = new_failure_counts()
    for fetcher in fetchers:
        stats = getattr(fetcher, "stats", None)
        if not callable(stats):
            continue
        with contextlib.suppress(Exception):
            counts = stats()
            if not isinstance(counts, dict):
                continue
            for key, value in counts.items():
                if key in totals:
                    totals[key] += value
    return totals


def close_all(fetchers: "Sequence[object]") -> None:
    """Fan out ``close()`` to every member fetcher that defines one.

    Shared by the expression- and sentence-audio chains. ``close()`` is
    optional/duck-typed, so members without it are skipped. Called between
    sequential mining runs to release per-run sockets / sqlite handles before
    the next run opens new ones (Windows back-to-back-mining freeze).
    """
    for fetcher in fetchers:
        close = getattr(fetcher, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


def first_candidate_hit(
    fetcher: "ExpressionAudioFetcher",
    candidates: list[tuple[str, str]],
    cancelled_check: Callable[[], bool] | None,
) -> Path | None:
    """Try each candidate via ``fetcher.fetch``, returning the first hit.

    Shared leaf implementation of ``fetch_candidates``: a single source
    exhausts its retry ladder (surface, katakana, lemma, ...) before the
    caller moves on.  Checks ``cancelled_check`` between candidates so a leaf
    used standalone honors cancellation like the composite does.
    """
    for mined_form, reading in candidates:
        if cancelled_check is not None and cancelled_check():
            return None
        result = fetcher.fetch(mined_form, reading, cancelled_check)
        if result is not None:
            return result
    return None

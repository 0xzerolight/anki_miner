"""Streaming HTTP downloader for recommended resources.

Fetches a URL to a uniquely-named ``.part`` temp file inside a caller-provided
directory and returns that temp path. It NEVER writes the final destination —
the caller routes the file to the right importer or validated destination.
GUI-free and importer-free by design.

The download pattern (browser User-Agent, ``raise_for_status``, chunked
``iter_content`` with a size cap, atomic staging via ``NamedTemporaryFile``)
mirrors ``JPod101AudioFetcher`` in ``expression_audio_fetcher.py``. Unlike that
fetcher, this function RAISES on failure (the worker catches per item).
"""

import contextlib
import hashlib
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO, cast

import requests

from anki_miner.exceptions import DownloadFailed, OperationCancelled, SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.services._install_common import cleanup_part
from anki_miner.services.download_resume import (
    CHECKPOINT_BYTES,
    CHECKPOINT_SECONDS,
    RestoredPartial,
    ResumeManifest,
    ResumeState,
    default_resume_root,
    is_identity_encoding,
    parse_content_range,
    strong_validator,
)

logger = logging.getLogger(__name__)

# Same browser UA as JPod101: some hosts/CDNs 403 the default python-requests UA.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Dictionary zips are large (Jitendex is tens of MB). 600 MB is a generous cap
# that still guards against a runaway/erroneous response.
MAX_DOWNLOAD_BYTES = 600 * 1024 * 1024

# (connect, read) timeout in seconds.
_TIMEOUT = (10, 60)

_CHUNK_SIZE = 8192

# Cap on how often progress reaches the caller. A 600 MB transfer at 8 KiB
# chunks is ~76,800 observations; delivering each one to a GUI slot costs more
# than the transfer does. Landmarks (phase start, first byte, final byte, retry,
# cancellation, failure) bypass the cap entirely, and the cancellation check
# still runs on EVERY raw chunk — throttling display must never throttle the
# response to Cancel.
PROGRESS_MIN_INTERVAL_S = 0.2

# Progress messages are deliberately URL-free: they reach user-facing labels,
# and a primary label reading "Downloading https://…" is what this replaces.
_DOWNLOADING = "Downloading"
_CANCELLED = "Download cancelled"
_FAILED = "Download failed"

# Ranged bytes are only meaningful when the body is stored bytes: a gzipped
# range would put our byte offsets in a different coordinate system than the
# server's. Ask for identity and refuse anything else on the resumed leg.
_IDENTITY_HEADERS = {"Accept-Encoding": "identity"}

# The only statuses that mean "your partial is not usable, fetch the whole thing
# again": the server ignored the range (200), the validator no longer holds
# (412), or the range is out of bounds (416). A 206 that fails its own checks
# joins them. Everything else is a transport or server fault, and answering one
# by discarding 580 MB is precisely the bug D16-C exists to fix.
_RESTART_CLEAN_STATUSES = frozenset({200, 206, 412, 416})

# Transient-failure retry policy (Issue #100: the reporter's JMdict download
# failed once on a flaky network and the wizard left them with no dictionary).
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (2.0, 5.0)  # sleep before attempt 2, attempt 3
# Cancellation poll granularity while backing off.
_BACKOFF_POLL_SECONDS = 0.2


def _is_retryable(exc: Exception) -> bool:
    """Whether *exc* is a transient failure worth another attempt.

    Ordering matters: ``HTTPError ⊂ RequestException ⊂ OSError``, so a naive
    ``isinstance(exc, OSError)`` predicate would retry permanent 4xx responses.
    Only 5xx HTTP errors and the transient transport set retry; everything
    else (4xx, malformed responses, local OS errors) fails immediately.
    """
    if isinstance(exc, requests.HTTPError):
        return exc.response is not None and exc.response.status_code >= 500
    return isinstance(
        exc,
        (
            requests.ConnectionError,
            requests.Timeout,
            # Mid-stream connection drop on a large transfer — NOT a
            # ConnectionError subclass despite the name.
            requests.exceptions.ChunkedEncodingError,
        ),
    )


def _new_session() -> requests.Session:
    """Build a freshly-configured ``requests.Session``.

    A NEW session per ``download_to_temp`` call (not a shared module-global):
    ``requests.Session`` is not safe for concurrent use, and two in-app pack
    downloads (e.g. the CUDA libs and the ONNX/VAD pack) can run on separate
    worker threads at the same time — each gating only its own button.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": _BROWSER_USER_AGENT})
    return session


CancelledCheck = Callable[[], bool]


class _ProgressGate:
    """Rate-limits a progress callback without ever dropping a landmark.

    Ordinary byte updates are worth showing about five times a second; the
    thousands in between say nothing a human can read. A landmark (``force``)
    is a *state change* rather than a number — it always goes through, and it
    restarts the window so the next ordinary update is not suppressed by an
    emit the caller did not ask to be throttled against.
    """

    def __init__(
        self,
        progress: DownloadProgressFn | None,
        *,
        min_interval_s: float | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._progress = progress
        # Resolved at construction, not import: the module constant is the
        # documented test seam for "prove the throttle actually throttles".
        self._min_interval_s = PROGRESS_MIN_INTERVAL_S if min_interval_s is None else min_interval_s
        self._clock = time.monotonic if clock is None else clock
        self._last_emit_at: float | None = None

    def emit(self, downloaded: int, total: int, message: str, *, force: bool = False) -> None:
        """Deliver one observation, unless it falls inside the current window."""
        if self._progress is None:
            return
        now = self._clock()
        if not force and self._last_emit_at is not None and now - self._last_emit_at < self._min_interval_s:
            return
        self._last_emit_at = now
        self._progress(downloaded, total, message)


def download_to_temp(
    url: str,
    *,
    dest_dir: Path,
    progress: DownloadProgressFn | None = None,
    cancelled_check: CancelledCheck | None = None,
    max_bytes: int | None = None,
    read_timeout_seconds: float | None = None,
    resume_key: str | None = None,
    resume_root: Path | None = None,
) -> Path:
    """Download *url* to a ``.part`` temp file in *dest_dir* and return it.

    Args:
        url: The resource URL to download.
        dest_dir: Directory to stage the temp file in (created if missing).
            Never the final destination — the caller routes the returned path.
        progress: Optional callback ``(downloaded_bytes, total_bytes_or_0,
            message)``. ``total`` is 0 when the server sends no Content-Length.
            Coalesced to at most one call per :data:`PROGRESS_MIN_INTERVAL_S`,
            except for the landmarks — phase start, first byte, final byte,
            retry, cancellation and failure — which always get through.
        cancelled_check: Optional zero-arg callable; when it returns True the
            partial temp file is removed and ``OperationCancelled("Download
            cancelled")`` is raised. Checked before the request and during
            chunk iteration.
        max_bytes: Hard size cap; the download is aborted with ``SetupError``
            once this many bytes have been received. ``None`` (the default)
            uses ``MAX_DOWNLOAD_BYTES`` (600 MB); callers fetching larger
            assets (e.g. multi-hundred-MB CUDA wheels) pass a higher value.
        read_timeout_seconds: Optional per-call read timeout. ``None`` keeps the
            shared 60-second default; cancellation-sensitive callers may pass a
            shorter interval so stalled reads return to their cancel check.
        resume_key: Stable, collision-free identifier for THIS artifact (D16-C).
            With one, the partial body survives cancellation, a transport drop
            and quitting the app, and the next call continues it — but only when
            the server proves the representation is unchanged; every other
            answer silently restarts from byte zero. Without one the transfer is
            all-or-nothing, exactly as before. Two different artifacts must
            never share a key.
        resume_root: Directory holding resume state. Defaults to
            ``<home>/runtime_state/downloads``.

    Returns:
        Path to the staged ``.part`` temp file.

    Raises:
        SetupError: On cancellation, HTTP error, size-cap exceeded, or any
            network/OS failure. The staged temp file is always cleaned up; the
            durable resume state survives only cancellation and retryable
            transport failures. The network/HTTP arm raises the ``DownloadFailed``
            subclass so a caller can tell an outage from bad bytes.
    """
    if max_bytes is None:
        max_bytes = MAX_DOWNLOAD_BYTES
    timeout = _TIMEOUT if read_timeout_seconds is None else (_TIMEOUT[0], read_timeout_seconds)

    dest_dir.mkdir(parents=True, exist_ok=True)

    state: ResumeState | None = None
    if resume_key is not None:
        state = ResumeState(default_resume_root() if resume_root is None else resume_root, resume_key)

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if cancelled_check is not None and cancelled_check():
            raise OperationCancelled("Download cancelled")
        try:
            return _download_once(
                url,
                dest_dir=dest_dir,
                progress=progress,
                cancelled_check=cancelled_check,
                max_bytes=max_bytes,
                timeout=timeout,
                state=state,
            )
        except OperationCancelled:
            # Never retried, and the partial stays: the user asked for a pause,
            # so the next launch offers to resume the bytes on disk.
            raise
        except SetupError:
            # Size-cap / truncation — never retried either, but here the bytes
            # on disk are not trustworthy.
            if state is not None:
                state.discard()
            raise
        except (requests.RequestException, OSError) as exc:
            if attempt < _MAX_ATTEMPTS and _is_retryable(exc):
                last_exc = exc
                logger.debug(
                    "resource download attempt %d/%d failed for %s: %s",
                    attempt,
                    _MAX_ATTEMPTS,
                    url,
                    exc,
                )
                if progress is not None:
                    progress(0, 0, f"Retrying download (attempt {attempt + 1}/{_MAX_ATTEMPTS})")
                _sleep_with_cancel(_BACKOFF_SECONDS[attempt - 1], cancelled_check)
                continue
            logger.debug("resource download failed for %s: %s", url, exc)
            # A transport failure we have run out of attempts for still leaves
            # trustworthy bytes behind: the next launch offers to resume them.
            # A permanent HTTP failure does not.
            if state is not None and not _is_retryable(exc):
                state.discard()
            # DownloadFailed, not a bare SetupError: this is the "the bytes
            # never arrived" arm, and a caller may legitimately treat an outage
            # differently from bytes that arrived wrong (the size-cap and
            # truncation raises above stay plain SetupError for that reason).
            # It IS a SetupError, so no existing handler changes.
            raise DownloadFailed(f"Failed to download {url}: {exc}") from exc

    # Unreachable: the final attempt either returned or raised above.
    raise DownloadFailed(f"Failed to download {url}: {last_exc}")


def _sleep_with_cancel(seconds: float, cancelled_check: CancelledCheck | None) -> None:
    """Back off for *seconds*, polling ``cancelled_check`` along the way."""
    waited = 0.0
    while waited < seconds:
        if cancelled_check is not None and cancelled_check():
            raise OperationCancelled("Download cancelled")
        time.sleep(_BACKOFF_POLL_SECONDS)
        waited += _BACKOFF_POLL_SECONDS


def _open_stream(
    session: requests.Session,
    url: str,
    timeout: tuple[float, float],
    state: ResumeState | None,
) -> tuple[requests.Response, RestoredPartial | None]:
    """Open the body, resuming only when the server proves nothing changed.

    Returns the streaming response and, when the response is a validated
    ``206``, the partial it continues. Every rejection path discards the stored
    partial and re-requests from byte zero — a ``200`` is never appended to.
    A 5xx on the ranged request is re-raised instead: that is a transient server
    fault, and throwing away 580 MB over one bad gateway is the bug D16-C exists
    to fix.
    """
    restored = state.restore(url) if state is not None else None
    if restored is None:
        response = session.get(url, headers=dict(_IDENTITY_HEADERS), timeout=timeout, stream=True)
        response.raise_for_status()
        return response, None

    manifest = restored.manifest
    headers = dict(_IDENTITY_HEADERS)
    headers["Range"] = f"bytes={manifest.length}-"
    if manifest.if_range:
        headers["If-Range"] = manifest.if_range
    response = session.get(url, headers=headers, timeout=timeout, stream=True)

    if _resume_is_provable(response, manifest):
        return response, restored

    if response.status_code not in _RESTART_CLEAN_STATUSES:
        # A 5xx is a transient server fault and a 4xx that is not a range
        # refusal is a permanent one. Neither says the artifact changed, so
        # neither is answered by re-fetching it here: the retry loop decides,
        # and it is the one that knows whether to keep the partial.
        response.raise_for_status()

    reason = f"status {response.status_code}"
    response.close()
    if state is not None:
        state.discard()
    logger.debug("resume rejected for %s (%s); restarting from byte zero", url, reason)
    response = session.get(url, headers=dict(_IDENTITY_HEADERS), timeout=timeout, stream=True)
    response.raise_for_status()
    return response, None


def _resume_is_provable(response: requests.Response, manifest: ResumeManifest) -> bool:
    """Whether ``response`` proves it continues exactly the bytes we kept.

    Only an exact ``206`` qualifies, and only with an unencoded body, a
    ``Content-Range`` starting at our durable length over an unchanged total,
    and an unchanged validator. ``200``, ``412``, ``416`` and every other answer
    are false — the caller then restarts clean.
    """
    if response.status_code != 206:
        return False
    if not is_identity_encoding(response.headers.get("Content-Encoding")):
        return False
    span = parse_content_range(response.headers.get("Content-Range"))
    if span is None:
        return False
    start, _end, total = span
    if start != manifest.length or total != manifest.total:
        return False
    return manifest.matches_response(
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
    )


def _download_once(
    url: str,
    *,
    dest_dir: Path,
    progress: DownloadProgressFn | None,
    cancelled_check: CancelledCheck | None,
    max_bytes: int,
    timeout: tuple[float, float] = _TIMEOUT,
    state: ResumeState | None = None,
) -> Path:
    """Single download attempt; raises raw transport exceptions for the retry loop."""
    tmp_path: Path | None = None
    gate = _ProgressGate(progress)
    # Hoisted so the terminal landmark in the handler can state where the
    # transfer actually stopped rather than starting again from zero.
    downloaded = 0
    total = 0
    terminal_reported = False
    try:
        with _new_session() as session:
            response, restored = _open_stream(session, url, timeout, state)
            try:
                if restored is not None:
                    # Continuing: the numbers the user sees, and the numbers the
                    # size cap is judged against, count the whole artifact.
                    downloaded = restored.manifest.length
                    total = restored.manifest.total
                    etag: str | None = restored.manifest.etag
                    last_modified: str | None = restored.manifest.last_modified
                    hasher = restored.hasher
                    keep_partial = True
                else:
                    total = int(response.headers.get("Content-Length") or 0)
                    etag, last_modified = strong_validator(
                        response.headers.get("ETag"), response.headers.get("Last-Modified")
                    )
                    hasher = hashlib.sha256()
                    keep_partial = state is not None and state.keepable(
                        total=total, etag=etag, last_modified=last_modified
                    )
                    if state is not None and not keep_partial:
                        # Nothing a later resume could prove — do not leave a
                        # partial behind that a future launch would offer.
                        state.discard()

                with _staged_body(dest_dir, state, resuming=restored is not None, keep=keep_partial) as staged:
                    tmp_path, body = staged
                    gate.emit(downloaded, total, _DOWNLOADING, force=True)  # Phase start.
                    first_chunk = True
                    checkpoint_at = time.monotonic() + CHECKPOINT_SECONDS
                    checkpoint_bytes = downloaded + CHECKPOINT_BYTES

                    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                        # Every raw chunk, never throttled: the display cadence
                        # must not become the cancellation cadence.
                        if cancelled_check is not None and cancelled_check():
                            if keep_partial and state is not None:
                                _checkpoint(state, body, hasher, url, total, downloaded, etag, last_modified)
                            terminal_reported = True
                            gate.emit(downloaded, total, _CANCELLED, force=True)
                            raise OperationCancelled("Download cancelled")
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise SetupError(f"Download exceeded size cap of {max_bytes} bytes: {url}")
                        body.write(chunk)
                        hasher.update(chunk)
                        now = time.monotonic()
                        if (
                            keep_partial
                            and state is not None
                            and (downloaded >= checkpoint_bytes or now >= checkpoint_at)
                        ):
                            _checkpoint(state, body, hasher, url, total, downloaded, etag, last_modified)
                            checkpoint_bytes = downloaded + CHECKPOINT_BYTES
                            checkpoint_at = now + CHECKPOINT_SECONDS
                        gate.emit(downloaded, total, _DOWNLOADING, force=first_chunk)
                        first_chunk = False

                    gate.emit(downloaded, total, _DOWNLOADING, force=True)  # Final byte.

                    # Belt-and-suspenders: requests/urllib3 already raise on a
                    # truncated Content-Length read, but assert the byte count too so
                    # a short response can never be promoted to a partial final file.
                    if total and downloaded != total:
                        raise SetupError(f"Download truncated: got {downloaded} of {total} bytes from {url}")
                    # A resumed artifact is spliced from two responses, so the
                    # length check above is the only thing standing between the
                    # user and a file built from two different releases. Refuse
                    # to hand one back without a declared total to check it with.
                    if restored is not None and not total:
                        raise SetupError(f"Resumed download cannot be verified without a total: {url}")
            finally:
                response.close()
    except BaseException:
        # Clean the staged .part on ANY failure, but re-raise RAW: the retry
        # loop in download_to_temp owns the retry decision and the terminal
        # SetupError wrapping. The landmark goes out first so a caller watching
        # only progress is never left on a mid-transfer number.
        if not terminal_reported:
            gate.emit(downloaded, total, _FAILED, force=True)
        # The durable partial is the retry loop's to keep or discard; a plain
        # staged temp file is always this attempt's litter.
        if tmp_path is not None and (state is None or state.part_path != tmp_path):
            cleanup_part(tmp_path)
        raise

    if state is not None and tmp_path is not None and state.part_path == tmp_path:
        return state.promote(_unique_part_path(dest_dir))
    assert tmp_path is not None
    return tmp_path


def _checkpoint(
    state: ResumeState,
    body: BinaryIO,
    hasher: "hashlib._Hash",
    url: str,
    total: int,
    length: int,
    etag: str | None,
    last_modified: str | None,
) -> None:
    """Make the bytes written so far durable and describe them atomically."""
    state.checkpoint(
        body,
        url=url,
        total=total,
        length=length,
        digest=hasher.copy().hexdigest(),
        etag=etag,
        last_modified=last_modified,
    )


def _unique_part_path(dest_dir: Path) -> Path:
    """Return an unused ``.part`` path in ``dest_dir``."""
    fd, name = tempfile.mkstemp(dir=dest_dir, suffix=".part")
    os.close(fd)
    return Path(name)


@contextlib.contextmanager
def _staged_body(
    dest_dir: Path,
    state: ResumeState | None,
    *,
    resuming: bool,
    keep: bool,
) -> Iterator[tuple[Path, BinaryIO]]:
    """Yield ``(path, handle)`` for the body being written.

    With a durable partial the body IS the resume ``.part`` — written in place
    so quitting leaves it where the next launch looks. Without one the transfer
    stages into ``dest_dir`` exactly as it did before D16-C.
    """
    if state is not None and keep:
        state.ensure_root()
        path = state.part_path
        handle: BinaryIO = path.open("r+b" if resuming else "wb")
        if resuming:
            handle.seek(0, os.SEEK_END)
        try:
            yield path, handle
        finally:
            handle.close()
        return

    with tempfile.NamedTemporaryFile(dir=dest_dir, suffix=".part", delete=False) as tmp_fd:
        yield Path(tmp_fd.name), cast(BinaryIO, tmp_fd)

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

import logging
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import requests

from anki_miner.exceptions import SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.services._install_common import cleanup_part

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
            partial temp file is removed and ``SetupError("Download
            cancelled")`` is raised. Checked before the request and during
            chunk iteration.
        max_bytes: Hard size cap; the download is aborted with ``SetupError``
            once this many bytes have been received. ``None`` (the default)
            uses ``MAX_DOWNLOAD_BYTES`` (600 MB); callers fetching larger
            assets (e.g. multi-hundred-MB CUDA wheels) pass a higher value.
        read_timeout_seconds: Optional per-call read timeout. ``None`` keeps the
            shared 60-second default; cancellation-sensitive callers may pass a
            shorter interval so stalled reads return to their cancel check.

    Returns:
        Path to the staged ``.part`` temp file.

    Raises:
        SetupError: On cancellation, HTTP error, size-cap exceeded, or any
            network/OS failure. The partial temp file is always cleaned up.
    """
    if max_bytes is None:
        max_bytes = MAX_DOWNLOAD_BYTES
    timeout = _TIMEOUT if read_timeout_seconds is None else (_TIMEOUT[0], read_timeout_seconds)

    dest_dir.mkdir(parents=True, exist_ok=True)

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if cancelled_check is not None and cancelled_check():
            raise SetupError("Download cancelled")
        try:
            return _download_once(
                url,
                dest_dir=dest_dir,
                progress=progress,
                cancelled_check=cancelled_check,
                max_bytes=max_bytes,
                timeout=timeout,
            )
        except SetupError:
            # Cancel / size-cap / truncation — never retried.
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
            raise SetupError(f"Failed to download {url}: {exc}") from exc

    # Unreachable: the final attempt either returned or raised above.
    raise SetupError(f"Failed to download {url}: {last_exc}")


def _sleep_with_cancel(seconds: float, cancelled_check: CancelledCheck | None) -> None:
    """Back off for *seconds*, polling ``cancelled_check`` along the way."""
    waited = 0.0
    while waited < seconds:
        if cancelled_check is not None and cancelled_check():
            raise SetupError("Download cancelled")
        time.sleep(_BACKOFF_POLL_SECONDS)
        waited += _BACKOFF_POLL_SECONDS


def _download_once(
    url: str,
    *,
    dest_dir: Path,
    progress: DownloadProgressFn | None,
    cancelled_check: CancelledCheck | None,
    max_bytes: int,
    timeout: tuple[float, float] = _TIMEOUT,
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
            response = session.get(url, timeout=timeout, stream=True)
            try:
                response.raise_for_status()

                total = int(response.headers.get("Content-Length") or 0)

                with tempfile.NamedTemporaryFile(dir=dest_dir, suffix=".part", delete=False) as tmp_fd:
                    tmp_path = Path(tmp_fd.name)
                    gate.emit(0, total, _DOWNLOADING, force=True)  # Phase start.
                    first_chunk = True

                    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                        # Every raw chunk, never throttled: the display cadence
                        # must not become the cancellation cadence.
                        if cancelled_check is not None and cancelled_check():
                            terminal_reported = True
                            gate.emit(downloaded, total, _CANCELLED, force=True)
                            raise SetupError("Download cancelled")
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise SetupError(f"Download exceeded size cap of {max_bytes} bytes: {url}")
                        tmp_fd.write(chunk)
                        gate.emit(downloaded, total, _DOWNLOADING, force=first_chunk)
                        first_chunk = False

                    gate.emit(downloaded, total, _DOWNLOADING, force=True)  # Final byte.

                    # Belt-and-suspenders: requests/urllib3 already raise on a
                    # truncated Content-Length read, but assert the byte count too so
                    # a short response can never be promoted to a partial final file.
                    if total and downloaded != total:
                        raise SetupError(f"Download truncated: got {downloaded} of {total} bytes from {url}")
            finally:
                response.close()
    except BaseException:
        # Clean the staged .part on ANY failure, but re-raise RAW: the retry
        # loop in download_to_temp owns the retry decision and the terminal
        # SetupError wrapping. The landmark goes out first so a caller watching
        # only progress is never left on a mid-transfer number.
        if not terminal_reported:
            gate.emit(downloaded, total, _FAILED, force=True)
        cleanup_part(tmp_path)
        raise

    # tmp_path is always set here: NamedTemporaryFile assigns it before any
    # statement that could leave the try block without raising.
    assert tmp_path is not None
    return tmp_path

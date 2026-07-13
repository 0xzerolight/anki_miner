"""Streaming HTTP downloader for recommended resources.

Fetches a URL to a uniquely-named ``.part`` temp file inside a caller-provided
directory and returns that temp path. It NEVER writes the final destination —
the caller routes the file to the right importer (dict/freq through their
importers, the raw pitch TSV via ``shutil.move``). GUI-free and importer-free
by design.

The download pattern (browser User-Agent, ``raise_for_status``, chunked
``iter_content`` with a size cap, atomic staging via ``NamedTemporaryFile``)
mirrors ``JPod101AudioFetcher`` in ``expression_audio_fetcher.py``. Unlike that
fetcher, this function RAISES on failure (the worker catches per item).
"""

import logging
import tempfile
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


def download_to_temp(
    url: str,
    *,
    dest_dir: Path,
    progress: DownloadProgressFn | None = None,
    cancelled_check: CancelledCheck | None = None,
    max_bytes: int | None = None,
) -> Path:
    """Download *url* to a ``.part`` temp file in *dest_dir* and return it.

    Args:
        url: The resource URL to download.
        dest_dir: Directory to stage the temp file in (created if missing).
            Never the final destination — the caller renames the returned path.
        progress: Optional callback ``(downloaded_bytes, total_bytes_or_0,
            message)`` invoked periodically. ``total`` is 0 when the server
            sends no Content-Length.
        cancelled_check: Optional zero-arg callable; when it returns True the
            partial temp file is removed and ``SetupError("Download
            cancelled")`` is raised. Checked before the request and during
            chunk iteration.
        max_bytes: Hard size cap; the download is aborted with ``SetupError``
            once this many bytes have been received. ``None`` (the default)
            uses ``MAX_DOWNLOAD_BYTES`` (600 MB); callers fetching larger
            assets (e.g. multi-hundred-MB CUDA wheels) pass a higher value.

    Returns:
        Path to the staged ``.part`` temp file.

    Raises:
        SetupError: On cancellation, HTTP error, size-cap exceeded, or any
            network/OS failure. The partial temp file is always cleaned up.
    """
    if max_bytes is None:
        max_bytes = MAX_DOWNLOAD_BYTES

    dest_dir.mkdir(parents=True, exist_ok=True)

    if cancelled_check is not None and cancelled_check():
        raise SetupError("Download cancelled")

    tmp_path: Path | None = None
    try:
        with _new_session() as session:
            response = session.get(url, timeout=_TIMEOUT, stream=True)
            try:
                response.raise_for_status()

                total = int(response.headers.get("Content-Length") or 0)

                with tempfile.NamedTemporaryFile(dir=dest_dir, suffix=".part", delete=False) as tmp_fd:
                    tmp_path = Path(tmp_fd.name)
                    downloaded = 0
                    if progress is not None:
                        progress(0, total, f"Downloading {url}")

                    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                        if cancelled_check is not None and cancelled_check():
                            raise SetupError("Download cancelled")
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise SetupError(f"Download exceeded size cap of {max_bytes} bytes: {url}")
                        tmp_fd.write(chunk)
                        if progress is not None:
                            progress(downloaded, total, f"Downloading {url}")

                    # Belt-and-suspenders: requests/urllib3 already raise on a
                    # truncated Content-Length read, but assert the byte count too so
                    # a short response can never be promoted to a partial final file.
                    if total and downloaded != total:
                        raise SetupError(f"Download truncated: got {downloaded} of {total} bytes from {url}")
            finally:
                response.close()
    except SetupError:
        cleanup_part(tmp_path)
        raise
    except (requests.RequestException, OSError) as exc:
        logger.debug("resource download failed for %s: %s", url, exc)
        cleanup_part(tmp_path)
        raise SetupError(f"Failed to download {url}: {exc}") from exc

    # tmp_path is always set here: NamedTemporaryFile assigns it before any
    # statement that could leave the try block without raising.
    assert tmp_path is not None
    return tmp_path

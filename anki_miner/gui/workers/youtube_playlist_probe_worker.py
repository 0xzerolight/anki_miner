"""Workers for resolving YouTube playlist metadata and probing per-entry video info.

``YouTubeFetcherService.probe_playlist`` and ``probe_metadata`` both spawn yt-dlp
and block on HTTP.  Running them on the Qt main thread freezes the GUI, so playlist
operations dispatch through these workers.

Two classes are provided:

* :class:`YouTubePlaylistResolveWorker` — a short-lived probe thread sharing
  :class:`~anki_miner.gui.workers.youtube_probe_worker._SingleCallProbeThread`
  with :class:`YouTubeProbeWorker`; it calls ``probe_playlist`` once and emits
  the resulting :class:`PlaylistInfo` or an error string.

* :class:`YouTubePlaylistProbeWorker` — a :class:`CancellableWorker` that
  iterates a list of video URLs sequentially, emitting ``entry_probed`` or
  ``entry_failed`` for each, and respecting cancellation between entries.

Shutdown guarantees
-------------------
*Resolve worker*: cancellation is not supported directly, but ``probe_playlist`` is
bounded by ``timeout_s`` (default 120 s).  A hung network causes the fetcher to kill
the yt-dlp subprocess and raise ``YouTubeFetchError``, surfaced via
``playlist_error``.  ``quit()`` + ``wait()`` will therefore return within ~timeout_s.

*Probe worker*: cancellation is checked between entries.  An in-flight
``probe_metadata`` call is bounded by its own subprocess timeout (``timeout_s``,
default 60 s), so ``wait()`` is bounded by ~timeout_s — the same guarantee as
:class:`YouTubeProbeWorker`.

Fetcher reference
-----------------
The fetcher is captured at construction time.  The YouTube tab may rebuild its
fetcher on settings changes; the worker holds its own reference and is unaffected —
same pattern as :class:`YouTubeProbeWorker`.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.gui.workers.youtube_probe_worker import _SingleCallProbeThread
from anki_miner.services.youtube_fetcher import YouTubeFetcherService


class YouTubePlaylistResolveWorker(_SingleCallProbeThread):
    """Run ``fetcher.probe_playlist(url, limit)`` in a background thread.

    Mirrors :class:`YouTubeProbeWorker` exactly — short-lived, no cancellation
    support, bounded by ``timeout_s`` — sharing the same
    :class:`_SingleCallProbeThread` body.

    Signals:
        playlist_resolved: Emitted with the :class:`PlaylistInfo` on success.
        playlist_error: Emitted with the exception message string on failure.
    """

    playlist_resolved = pyqtSignal(object)  # PlaylistInfo
    playlist_error = pyqtSignal(str)

    def __init__(
        self,
        fetcher: YouTubeFetcherService,
        url: str,
        limit: int,
        parent: object = None,
        timeout_s: float = 120.0,
    ) -> None:
        """Initialize the resolve worker.

        Args:
            fetcher: Fetcher service used to probe the playlist.
            url: YouTube playlist URL to resolve.
            limit: Maximum number of entries to retrieve.
            parent: Optional parent QObject.
            timeout_s: Hard upper bound on the probe subprocess, in seconds.
                Forwarded to ``YouTubeFetcherService.probe_playlist``. On
                timeout, the fetcher kills the yt-dlp subprocess and raises
                ``YouTubeFetchError``.
        """
        super().__init__(fetcher, timeout_s=timeout_s, parent=parent)
        self._url = url
        self._limit = limit

    def _do_call(self) -> object:
        return self._fetcher.probe_playlist(self._url, self._limit, timeout_s=self._timeout_s)

    def _emit_result(self, result: object) -> None:
        self.playlist_resolved.emit(result)

    def _emit_error(self, message: str) -> None:
        self.playlist_error.emit(message)


class YouTubePlaylistProbeWorker(CancellableWorker):
    """Sequentially probe full metadata for playlist entries in one thread.

    Iterates a list of video URLs, calling ``fetcher.probe_metadata`` for each.
    Cancellation is checked between entries; an in-flight ``probe_metadata`` call
    is bounded by its subprocess timeout, so ``wait()`` after ``cancel()`` is
    bounded by ~``timeout_s``.

    Signals:
        entry_probed: Emitted as ``(idx, VideoInfo)`` on success.
        entry_failed: Emitted as ``(idx, error_message)`` on failure.
            Processing continues to the next entry after a failure.
    """

    entry_probed = pyqtSignal(int, object)  # (idx, VideoInfo)
    entry_failed = pyqtSignal(int, str)  # (idx, error message)

    def __init__(
        self,
        fetcher: YouTubeFetcherService,
        urls: list[str],
        parent: object = None,
        timeout_s: float = 60.0,
    ) -> None:
        """Initialize the probe worker.

        Args:
            fetcher: Fetcher service used to probe each entry's metadata.
            urls: List of YouTube video URLs to probe in order.
            parent: Optional parent QObject.
            timeout_s: Hard upper bound on each ``probe_metadata`` subprocess
                call, in seconds.  On timeout, the fetcher kills the yt-dlp
                subprocess and raises ``YouTubeFetchError``.
        """
        super().__init__(parent)
        self._fetcher = fetcher
        self._urls = list(urls)
        self._timeout_s = timeout_s

    def run(self) -> None:
        """Probe each URL sequentially, emitting signals per entry.

        Cancellation is polled before each entry.  Failures emit
        ``entry_failed`` and processing continues to the next URL.
        """
        for idx, url in enumerate(self._urls):
            if self.is_cancelled:
                return
            try:
                info = self._fetcher.probe_metadata(url, timeout_s=self._timeout_s)
                self.entry_probed.emit(idx, info)
            except Exception as exc:  # noqa: BLE001 - incl. VideoTooLongError, YouTubeFetchError
                self.entry_failed.emit(idx, str(exc))

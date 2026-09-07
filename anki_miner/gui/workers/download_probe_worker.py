"""Short-lived probe threads for the Download tool's pickers.

``MediaDownloaderService.probe_tracks`` and ``.probe_playlist`` spawn yt-dlp and
block on HTTP; running either on the Qt main thread freezes the GUI. Both are
bounded by their own subprocess timeout, so ``quit()`` + ``wait()`` returns
within ``timeout_s`` and neither worker needs cancellation support.

Separate from ``youtube_probe_worker``: that module's shared body is typed to
``YouTubeFetcherService`` and redacts YouTube URLs specifically, while the
Download tool accepts any site yt-dlp supports.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.audio_fetch_common import redact_url_for_log
from anki_miner.services.media_downloader import MediaDownloaderService

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 120.0


class _DownloadProbeThread(CancellableWorker):
    """Shared body: run one blocking service call, emit result or error.

    Subclasses declare their own success signal and implement :meth:`_do_call`
    and :meth:`_emit_result`; the error signal is shared because every caller
    treats a probe failure the same way — a screen issue and a re-enabled
    button.
    """

    probe_error = pyqtSignal(str)

    def __init__(
        self,
        service: MediaDownloaderService,
        url: str,
        *,
        timeout_s: float = _PROBE_TIMEOUT_S,
        parent: object = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._url = url
        self._timeout_s = timeout_s

    def run(self) -> None:
        """Execute the probe and emit the appropriate signal."""
        self.log_start(type(self).__name__, url=redact_url_for_log(self._url), timeout_s=self._timeout_s)
        try:
            result = self._do_call()
        except Exception as exc:  # noqa: BLE001 - surface every failure to the GUI
            # type(self).__name__, not a literal: both workers share this body,
            # and a hardcoded context would file every failure under one name.
            self.report_failure(exc, context=type(self).__name__, on_error=self.probe_error.emit)
        else:
            self._emit_result(result)
            self.log_end(url=redact_url_for_log(self._url), results=1)

    def _do_call(self) -> object:  # pragma: no cover - abstract
        raise NotImplementedError

    def _emit_result(self, result: object) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class DownloadTracksProbeWorker(_DownloadProbeThread):
    """Fetch the subtitle/audio languages one URL offers.

    Signals:
        tracks_probed: Emitted with the :class:`UrlTracks` on success.
        probe_error: Emitted with the failure message on error.
    """

    tracks_probed = pyqtSignal(object)  # UrlTracks

    def _do_call(self) -> object:
        return self._service.probe_tracks(self._url, timeout_s=self._timeout_s)

    def _emit_result(self, result: object) -> None:
        self.tracks_probed.emit(result)


class DownloadPlaylistResolveWorker(_DownloadProbeThread):
    """List a playlist's entries.

    Signals:
        playlist_resolved: Emitted with the :class:`DownloadPlaylist` on success.
        probe_error: Emitted with the failure message on error.
    """

    playlist_resolved = pyqtSignal(object)  # DownloadPlaylist

    def __init__(
        self,
        service: MediaDownloaderService,
        url: str,
        limit: int,
        *,
        timeout_s: float = _PROBE_TIMEOUT_S,
        parent: object = None,
    ) -> None:
        super().__init__(service, url, timeout_s=timeout_s, parent=parent)
        self._limit = limit

    def _do_call(self) -> object:
        return self._service.probe_playlist(self._url, limit=self._limit, timeout_s=self._timeout_s)

    def _emit_result(self, result: object) -> None:
        self.playlist_resolved.emit(result)

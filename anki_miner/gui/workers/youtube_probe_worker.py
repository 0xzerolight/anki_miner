"""Short-lived worker thread for probing YouTube video metadata.

``YouTubeFetcherService.probe_metadata`` spawns yt-dlp and blocks for a few
seconds on HTTP. Running it on the Qt main thread freezes the GUI, so the
YouTube tab dispatches probes through this minimal ``QThread`` and listens for
``probe_done`` / ``probe_error`` signals.
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from anki_miner.services.youtube_fetcher import YouTubeFetcherService


class YouTubeProbeWorker(QThread):
    """Run ``fetcher.probe_metadata(url)`` in a background thread.

    Cancellation is not supported — probes are short (<60s) and the main
    window's close handler can call :meth:`quit` + :meth:`wait` if needed.
    """

    probe_done = pyqtSignal(object)  # VideoInfo
    probe_error = pyqtSignal(str)

    def __init__(
        self,
        fetcher: YouTubeFetcherService,
        url: str,
        parent: object = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._fetcher = fetcher
        self._url = url

    def run(self) -> None:
        """Execute the probe and emit the appropriate signal."""
        try:
            info = self._fetcher.probe_metadata(self._url)
            self.probe_done.emit(info)
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            self.probe_error.emit(str(exc))

"""Short-lived worker thread for probing YouTube video metadata.

``YouTubeFetcherService.probe_metadata`` spawns yt-dlp and blocks on HTTP.
Running it on the Qt main thread freezes the GUI, so the YouTube tab dispatches
probes through this minimal ``QThread`` and listens for
``probe_done`` / ``probe_error`` signals.

The probe is bounded by a hard timeout (``timeout_s``, default 60s) enforced by
the fetcher's subprocess call. A hung network no longer blocks indefinitely:
the fetcher kills the yt-dlp process tree and raises ``YouTubeFetchError``,
which this worker surfaces via ``probe_error``.
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from anki_miner.services.youtube_fetcher import YouTubeFetcherService


class YouTubeProbeWorker(QThread):
    """Run ``fetcher.probe_metadata(url)`` in a background thread.

    Cancellation is not supported directly, but the underlying
    ``probe_metadata`` call is bounded by ``timeout_s`` (default 60s). If the
    probe hangs on the network, the fetcher kills the subprocess tree and
    raises ``YouTubeFetchError``, which is re-emitted as ``probe_error``. The
    main window's close handler can therefore safely call :meth:`quit` +
    :meth:`wait` knowing the worker will exit within the timeout window.
    """

    probe_done = pyqtSignal(object)  # VideoInfo
    probe_error = pyqtSignal(str)

    def __init__(
        self,
        fetcher: YouTubeFetcherService,
        url: str,
        parent: object = None,
        timeout_s: float = 60.0,
    ) -> None:
        """Initialize the probe worker.

        Args:
            fetcher: Fetcher service used to probe metadata.
            url: YouTube URL to probe.
            parent: Optional parent QObject.
            timeout_s: Hard upper bound on the probe subprocess, in seconds.
                Forwarded to ``YouTubeFetcherService.probe_metadata``. On
                timeout, the fetcher kills the yt-dlp process tree and raises
                ``YouTubeFetchError``.
        """
        super().__init__(parent)  # type: ignore[arg-type]
        self._fetcher = fetcher
        self._url = url
        self._timeout_s = timeout_s

    def run(self) -> None:
        """Execute the probe and emit the appropriate signal.

        A timeout in the fetcher raises ``YouTubeFetchError``, which is caught
        by the broad ``except`` below and surfaced via ``probe_error``.
        """
        try:
            info = self._fetcher.probe_metadata(self._url, timeout_s=self._timeout_s)
            self.probe_done.emit(info)
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            self.probe_error.emit(str(exc))

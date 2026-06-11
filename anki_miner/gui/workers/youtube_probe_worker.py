"""Short-lived worker threads for probing YouTube metadata.

``YouTubeFetcherService.probe_metadata`` (and ``probe_playlist``) spawn yt-dlp
and block on HTTP. Running them on the Qt main thread freezes the GUI, so the
YouTube tab dispatches probes through minimal ``QThread`` subclasses and listens
for their done / error signals.

Each probe is bounded by a hard timeout (``timeout_s``) enforced by the
fetcher's subprocess call. A hung network no longer blocks indefinitely: the
fetcher kills the yt-dlp subprocess and raises ``YouTubeFetchError``, which the
worker surfaces via its error signal.

:class:`_SingleCallProbeThread` factors the shared "capture the fetcher, run one
call, emit done-or-error" body; the two public probe workers
(:class:`YouTubeProbeWorker` here and ``YouTubePlaylistResolveWorker`` in
``youtube_playlist_probe_worker``) are thin subclasses declaring their own
signals and the single call they make.
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from anki_miner.services.youtube_fetcher import YouTubeFetcherService


class _SingleCallProbeThread(QThread):
    """Shared body for single yt-dlp probe threads.

    Captures the fetcher at construction time (the YouTube tab may rebuild its
    fetcher on settings changes; the worker holds its own reference and is
    unaffected) and runs one blocking call in :meth:`run`, emitting the result
    via :meth:`_emit_result` or the failure message via :meth:`_emit_error`.

    Cancellation is not supported directly, but the underlying call is bounded
    by ``timeout_s``; on timeout the fetcher kills the yt-dlp subprocess and
    raises ``YouTubeFetchError``, surfaced as an error signal. The close handler
    can therefore call :meth:`quit` + :meth:`wait` knowing the worker exits
    within the timeout window.

    Subclasses declare their own done/error signals and implement
    :meth:`_do_call` / :meth:`_emit_result` / :meth:`_emit_error`.
    """

    def __init__(self, fetcher: YouTubeFetcherService, *, timeout_s: float, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._fetcher = fetcher
        self._timeout_s = timeout_s

    def run(self) -> None:
        """Execute the probe and emit the appropriate signal."""
        try:
            result = self._do_call()
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            self._emit_error(str(exc))
        else:
            self._emit_result(result)

    def _do_call(self) -> object:  # pragma: no cover - abstract
        raise NotImplementedError

    def _emit_result(self, result: object) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _emit_error(self, message: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class YouTubeProbeWorker(_SingleCallProbeThread):
    """Run ``fetcher.probe_metadata(url)`` in a background thread.

    Emits :data:`probe_done` with the resulting ``VideoInfo`` on success, or
    :data:`probe_error` with the exception message on failure. See
    :class:`_SingleCallProbeThread` for the shutdown guarantees.
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
                timeout, the fetcher kills the yt-dlp subprocess and raises
                ``YouTubeFetchError``.
        """
        super().__init__(fetcher, timeout_s=timeout_s, parent=parent)
        self._url = url

    def _do_call(self) -> object:
        return self._fetcher.probe_metadata(self._url, timeout_s=self._timeout_s)

    def _emit_result(self, result: object) -> None:
        self.probe_done.emit(result)

    def _emit_error(self, message: str) -> None:
        self.probe_error.emit(message)

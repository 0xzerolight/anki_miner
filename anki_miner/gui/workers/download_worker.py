"""Worker for the Utilities → Download tool: sequential yt-dlp downloads.

One :class:`~anki_miner.services.media_downloader.MediaDownloaderService` call
per URL, mapped onto the shared :class:`FileQueueWorker` 5-signal contract.
Downloads land in the user's chosen folder and are never deleted by the app.
"""

from __future__ import annotations

import logging
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.base import AnkiMinerException
from anki_miner.exceptions.youtube import YtdlpNotFoundError
from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.services.audio_fetch_common import redact_url_for_log
from anki_miner.services.media_downloader import (
    DownloadOptions,
    DownloadStatus,
    MediaDownloaderService,
)
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class DownloadWorker(FileQueueWorker):
    """Download each queued URL into *dest_dir* via yt-dlp.

    Per-URL failures are forwarded as ``file_finished`` errors and the queue
    continues; a missing yt-dlp binary dooms every remaining URL, so it stops
    the whole queue (``is_cancelled`` stays False — a tool error, not a user
    cancel). An "already downloaded" result maps to ``file_skipped``, so re-runs
    of the same list are cheap and never silent.
    """

    #: A missing yt-dlp executable affects every remaining URL.
    _FATAL_QUEUE_EXCEPTIONS = (YtdlpNotFoundError,)

    def __init__(
        self,
        config: AnkiMinerConfig,
        urls: list[str],
        *,
        dest_dir: Path,
        options: DownloadOptions,
        service: MediaDownloaderService | None = None,
        parent=None,
    ) -> None:
        """Initialise the worker."""
        super().__init__(parent)
        self._config = config
        self._urls = list(urls)
        self._dest_dir = dest_dir
        self._options = options
        self._service = MediaDownloaderService(config) if service is None else service

    def _queue_items(self) -> list[str]:
        return self._urls

    def _process_item(self, idx: int, url: str) -> None:
        self._dest_dir.mkdir(parents=True, exist_ok=True)

        def _progress(message: str, frac: float | None) -> None:
            if frac is not None:
                pct = int(frac * 100)
                self.file_progress.emit(idx, pct, tr_format(self.tr("%1: %2%"), message, pct))
            else:
                self.file_progress.emit(idx, 0, message)

        try:
            result = self._service.download(
                url,
                self._dest_dir,
                self._options,
                progress_cb=_progress,
                cancel_event=self._cancel_event,
            )
        except YtdlpNotFoundError:
            # Re-raise for the base loop's fatal-queue stop.
            raise
        except AnkiMinerException as exc:
            logger.warning("download_worker: %s failed: %s", redact_url_for_log(url), exc)
            if not self.is_cancelled:
                self.file_finished.emit(idx, None, str(exc))
            return

        if result.status is DownloadStatus.DONE:
            self.file_progress.emit(idx, 100, self.tr("Done"))
            self.file_finished.emit(idx, result.filepath, None)
        elif result.status is DownloadStatus.ALREADY_DOWNLOADED:
            msg = self.tr("Already downloaded")
            self.file_progress.emit(idx, 100, msg)
            self.file_skipped.emit(idx, result.filepath, msg)
        elif result.status is DownloadStatus.CANCELLED:
            self.file_finished.emit(idx, None, self.tr("Cancelled"))
        else:  # pragma: no cover - exhaustiveness guard
            raise ValueError(f"Unsupported download status: {result.status!r}")

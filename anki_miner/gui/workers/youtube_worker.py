"""Worker thread for YouTube video mining.

Owns the per-run workspace lifecycle: allocates a fresh directory under
``config.media_temp_folder / "youtube" / run-<hex>``, delegates to
``EpisodeProcessor.process_youtube_url``, and removes the directory on every
exit path (success, error, cancellation).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.models.youtube import SubMode
from anki_miner.orchestration import EpisodeProcessor


class YouTubeWorkerThread(CancellableWorker):
    """Worker thread that fetches a YouTube video and mines it into Anki cards.

    Owns the workspace lifecycle: allocates a fresh per-run directory under
    ``config.media_temp_folder / "youtube"``, passes it to
    ``EpisodeProcessor.process_youtube_url``, and deletes it on every exit
    path (success, error, cancellation).

    Inherits thread-safe cancellation (``_cancel_event`` / ``is_cancelled``)
    from :class:`CancellableWorker`.
    """

    # ProcessingResult from the orchestrator; ``object`` avoids a Qt meta-type
    # registration for the dataclass.
    result_ready = pyqtSignal(object)
    # (label, percentage) where percentage == -1 signals indeterminate.
    progress = pyqtSignal(str, int)

    def __init__(
        self,
        processor: EpisodeProcessor,
        config: AnkiMinerConfig,
        url: str,
        video_id: str,
        sub_mode: SubMode,
        parent=None,
    ) -> None:
        """Initialize the YouTube worker thread.

        Args:
            processor: Episode processor instance (used for ``process_youtube_url``).
            config: Frozen app config; ``media_temp_folder`` is the workspace root.
            url: Full YouTube URL passed through to the fetcher.
            video_id: Canonical video id; used by the fetcher to glob outputs.
            sub_mode: ``"manual_only"`` or ``"auto_only"``.
            parent: Optional parent QObject.
        """
        super().__init__(parent)
        self._processor = processor
        self._config = config
        self._url = url
        self._video_id = video_id
        self._sub_mode = sub_mode

    def run(self) -> None:
        """Execute the YouTube fetch + mining pipeline in the background."""
        workspace: Path | None = None
        try:
            if self.is_cancelled:
                return

            workspace = self._config.media_temp_folder / "youtube" / f"run-{uuid4().hex}"
            workspace.mkdir(parents=True, exist_ok=True)

            result = self._processor.process_youtube_url(
                url=self._url,
                video_id=self._video_id,
                workspace=workspace,
                sub_mode=self._sub_mode,
                cancel_event=self._cancel_event,
                # ``process_youtube_url`` annotates the callback as
                # ``ProgressCallback``, but internally forwards it to
                # ``YouTubeFetcherService.fetch_video`` which requires a
                # ``Callable[[str, float | None], None]`` (see the matching
                # type-ignore in episode_processor.process_youtube_url). We
                # pass the (label, frac) callable intentionally.
                progress_callback=self._emit_progress,  # type: ignore[arg-type]
            )

            if not self.is_cancelled:
                self.result_ready.emit(result)
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            if not self.is_cancelled:
                self.error.emit(str(exc))
        finally:
            if workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)

    def _emit_progress(self, label: str, frac: float | None) -> None:
        """Translate fetcher/orchestrator progress into the Qt ``progress`` signal.

        ``frac`` is a float in [0.0, 1.0] when a determinate fraction is known,
        or ``None`` for indeterminate stages (e.g. "Merging"). Indeterminate
        progress is emitted as ``-1``.
        """
        if frac is None:
            pct = -1
        else:
            # Clamp defensively; yt-dlp occasionally emits tail values >1.0.
            clamped = max(0.0, min(1.0, frac))
            pct = int(round(clamped * 100))
        self.progress.emit(label, pct)

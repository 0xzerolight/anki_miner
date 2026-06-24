"""Worker that retimes subtitle files to a list of video files using alass.

Signal contract (frozen — mirrors SubtitleGenWorker):
    ``file_started(int)``                      — emitted at the start of each pair (idx)
    ``file_progress(int, int, str)``           — (idx, pct 0-100, message) during retiming
    ``file_finished(int, object, object)``     — (idx, out_path|None, error_str|None)
    ``file_skipped(int, object)``              — (idx, out_path) when output exists and overwrite is False
    ``queue_finished()``                       — emitted once after the last pair
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class SubtitleRetimeWorker(CancellableWorker):
    """Retime a list of (video, subtitle) pairs using alass.

    Per pair:
    1. Emits ``file_started(idx)``.
    2. Determines output path: ``video.stem + in_sub.suffix``, in *output_dir* if
       given, else next to the video.
    3. If the output already exists and *overwrite* is False — emits
       ``file_finished(idx, out_sub, None)`` and continues.
    4. Calls the retimer; forwards alass stdout lines via ``file_progress``.
    5. Emits ``file_finished(idx, out_sub, None)`` on success, or
       ``file_finished(idx, None, error_str)`` on failure / cancel.

    ``AlassNotFoundError`` stops the entire queue (alass is missing for all
    subsequent pairs too) after emitting a per-pair error for the triggering
    pair.  All other unexpected exceptions are caught per-pair so the queue
    continues.

    Cancel is honoured between pairs and propagated into the retimer via
    ``self._cancel_event``.

    After the loop, ``queue_finished()`` is emitted unconditionally.

    Args:
        config: Frozen :class:`~anki_miner.config.AnkiMinerConfig` instance.
        pairs: Ordered list of ``(video_path, subtitle_path)`` pairs.
        output_dir: When given, output subtitles are written here instead of
            next to each source video.
        overwrite: When ``True``, existing output subtitles are regenerated.
        split_penalty: alass ``--split-penalty`` value (0–1000, default 7).
        retimer: Optional callable with the same signature as
            :func:`~anki_miner.services.subtitle_retimer.retime_subtitle`;
            defaults to that function.  Injected by tests.
        parent: Optional parent QObject.
    """

    #: Emitted at the start of each pair; argument is the 0-based pair index.
    file_started = pyqtSignal(int)
    #: (idx, pct 0-100, message) — progress within a single pair.
    file_progress = pyqtSignal(int, int, str)
    #: (idx, out_path|None, error_str|None) — outcome for each pair.
    file_finished = pyqtSignal(int, object, object)
    #: (idx, out_path) — emitted when the output already exists and overwrite is False.
    file_skipped = pyqtSignal(int, object)
    #: Emitted once after all pairs have been processed (or skipped / errored).
    queue_finished = pyqtSignal()

    def __init__(
        self,
        config,
        pairs: list[tuple[Path, Path]],
        *,
        output_dir: Path | None = None,
        overwrite: bool = False,
        split_penalty: float = 7,
        retimer=None,
        parent=None,
    ) -> None:
        """Initialise the worker."""
        super().__init__(parent)
        self._config = config
        self._pairs = list(pairs)
        self._output_dir = output_dir
        self._overwrite = overwrite
        self._split_penalty = split_penalty
        # Set when alass is missing: stops the queue without poisoning
        # is_cancelled (a tool error, not a user cancel).
        self._stop_queue = False

        if retimer is None:
            from anki_miner.services.subtitle_retimer import retime_subtitle

            self._retimer = retime_subtitle
        else:
            self._retimer = retimer

    def run(self) -> None:
        """Execute retiming for all pairs in the background thread."""
        try:
            self._process_queue()
        finally:
            self.queue_finished.emit()

    def _process_queue(self) -> None:
        for idx, (video, in_sub) in enumerate(self._pairs):
            if self.is_cancelled or self._stop_queue:
                break

            self.file_started.emit(idx)

            # Determine output path: video stem + subtitle extension.
            name = video.stem + in_sub.suffix
            out_sub = self._output_dir / name if self._output_dir is not None else video.parent / name

            # Skip-if-exists logic.
            if out_sub.exists() and not self._overwrite:
                logger.debug("subtitle_retime_worker: skipped %s (exists)", out_sub)
                self.file_progress.emit(idx, 100, self.tr("Skipped, exists"))
                self.file_skipped.emit(idx, out_sub)
                continue

            # Ensure output directory exists before writing.
            if self._output_dir is not None:
                self._output_dir.mkdir(parents=True, exist_ok=True)

            self._process_pair(idx, video, in_sub, out_sub)

    def _process_pair(self, idx: int, video: Path, in_sub: Path, out_sub: Path) -> None:
        """Process a single (video, subtitle) pair; never raises (errors forwarded as signals)."""
        try:
            # log_cb forwards alass stdout lines via file_progress.
            # alass provides no percentage — emit pct=0 for in-progress lines.
            def _log_cb(line: str) -> None:
                self.file_progress.emit(idx, 0, line)

            ok = self._retimer(
                self._config,
                video,
                in_sub,
                out_sub,
                split_penalty=self._split_penalty,
                cancel_event=self._cancel_event,
                log_cb=_log_cb,
            )

            if ok:
                self.file_progress.emit(idx, 100, self.tr("Done"))
                self.file_finished.emit(idx, out_sub, None)
            else:
                if self.is_cancelled:
                    self.file_finished.emit(idx, None, self.tr("Cancelled"))
                else:
                    self.file_finished.emit(
                        idx,
                        None,
                        tr_format(self.tr("Retiming failed for %1"), video.name),
                    )

        except AlassNotFoundError as exc:
            # alass missing — affects all remaining pairs; stop the queue.
            # Report the real alass error for this pair FIRST, then flag the
            # queue to stop. Do NOT touch _cancel_event: is_cancelled must stay
            # False so callers can tell a tool error from a user cancel.
            self.file_finished.emit(idx, None, str(exc))
            self._stop_queue = True

        except Exception as exc:  # noqa: BLE001 — per-pair isolation
            logger.exception("subtitle_retime_worker: error on %s", video)
            self.file_finished.emit(idx, None, str(exc))

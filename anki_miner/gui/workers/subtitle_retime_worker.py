"""Worker that retimes subtitle files to a list of video files using alass.

The 5-signal contract and per-file queue loop live in
:class:`~anki_miner.gui.workers.file_queue_worker.FileQueueWorker`; this worker
supplies only the per-pair retiming logic and declares ``AlassNotFoundError`` as
a queue-stopping fatal exception.
"""

from __future__ import annotations

import logging
from pathlib import Path

from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.utils.file_pairing import resolve_output_path
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class SubtitleRetimeWorker(FileQueueWorker):
    """Retime a list of (video, subtitle) pairs using alass.

    Per pair:
    1. Emits ``file_started(idx)``.
    2. Determines output path: ``video.stem + in_sub.suffix``, in *output_dir* if
       given, else next to the video.
    3. If the output already exists and *overwrite* is False — emits
       ``file_skipped(idx, out_sub)`` and continues.
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
        disable_fps_guessing: Pass ``--disable-fps-guessing`` to alass (default
            True — stops bogus framerate stretching of an already-good sub).
        no_split: Pass ``--no-split`` to alass (single global offset only).
        audio_track_override: Audio-stream index to align against; None
            auto-detects the Japanese track per video.
        retimer: Optional callable with the same signature as
            :func:`~anki_miner.services.subtitle_retimer.retime_subtitle`;
            defaults to that function.  Injected by tests.
        parent: Optional parent QObject.
    """

    #: alass missing dooms every remaining pair — stop the queue (see base loop).
    _FATAL_QUEUE_EXCEPTIONS = (AlassNotFoundError,)

    def __init__(
        self,
        config,
        pairs: list[tuple[Path, Path]],
        *,
        output_dir: Path | None = None,
        overwrite: bool = False,
        split_penalty: float = 7,
        disable_fps_guessing: bool = True,
        no_split: bool = False,
        audio_track_override: int | None = None,
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
        self._disable_fps_guessing = disable_fps_guessing
        self._no_split = no_split
        self._audio_track_override = audio_track_override

        if retimer is None:
            from anki_miner.services.subtitle_retimer import retime_subtitle

            self._retimer = retime_subtitle
        else:
            self._retimer = retimer

    def _queue_items(self) -> list[tuple[Path, Path]]:
        return self._pairs

    def _process_item(self, idx: int, item: tuple[Path, Path]) -> None:
        video, in_sub = item

        # Determine output path: video stem + subtitle extension, resolved
        # against existing on-disk files so an overwrite replaces a
        # visually-identical (NFC/NFD- or case-variant) subtitle in place
        # instead of spawning a Windows duplicate. See resolve_output_path.
        name = video.stem + in_sub.suffix
        out_dir = self._output_dir if self._output_dir is not None else video.parent
        out_sub = resolve_output_path(out_dir, name)

        # Skip-if-exists logic. When the resolved output is the INPUT subtitle
        # itself (a sub already named ``<video stem><suffix>`` next to the
        # video), the generic "exists" skip misleadingly reports the input as a
        # pre-existing output. The retimer supports safe in-place aliasing, so
        # tell the user to enable Overwrite rather than implying a stale twin.
        if out_sub.exists() and not self._overwrite:
            if self._aliases_input(out_sub, in_sub):
                logger.debug("subtitle_retime_worker: skipped %s (output equals input)", out_sub)
                msg = self.tr("Output equals input; enable Overwrite to retime in place")
            else:
                logger.debug("subtitle_retime_worker: skipped %s (exists)", out_sub)
                msg = self.tr("Skipped, exists")
            self.file_progress.emit(idx, 100, msg)
            self.file_skipped.emit(idx, out_sub)
            return

        # Ensure output directory exists before writing.
        if self._output_dir is not None:
            self._output_dir.mkdir(parents=True, exist_ok=True)

        self._process_pair(idx, video, in_sub, out_sub)

    @staticmethod
    def _aliases_input(out_sub: Path, in_sub: Path) -> bool:
        """Return True when the resolved output path is the input subtitle itself.

        Uses ``samefile`` to catch on-disk aliases (symlink / NFC-NFD or
        case-variant names that ``resolve_output_path`` may have collapsed onto
        the input) and falls back to path equality; ``samefile`` needs both paths
        to exist, so a missing input degrades to the equality check.
        """
        if out_sub == in_sub:
            return True
        try:
            return out_sub.samefile(in_sub)
        except OSError:
            return False

    def _process_pair(self, idx: int, video: Path, in_sub: Path, out_sub: Path) -> None:
        """Process a single (video, subtitle) pair.

        Per-pair errors are forwarded as signals; only a ``_FATAL_QUEUE_EXCEPTIONS``
        member (alass missing) propagates, for the base loop to stop the queue.
        """
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
                disable_fps_guessing=self._disable_fps_guessing,
                no_split=self._no_split,
                audio_track_override=self._audio_track_override,
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

        except self._FATAL_QUEUE_EXCEPTIONS:
            # alass missing — affects all remaining pairs. Re-raise so the base
            # queue loop reports this pair's error and stops the queue without
            # poisoning is_cancelled (a tool error, not a user cancel).
            raise

        except Exception as exc:  # noqa: BLE001 — per-pair isolation
            logger.exception("subtitle_retime_worker: error on %s", video)
            self.file_finished.emit(idx, None, str(exc))

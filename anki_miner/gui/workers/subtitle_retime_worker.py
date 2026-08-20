"""Worker that retimes subtitle files to a list of video files.

The 5-signal contract and per-file queue loop live in
:class:`~anki_miner.gui.workers.file_queue_worker.FileQueueWorker`; this worker
supplies only the per-pair retiming logic. The retime pipeline itself (engine
chain, validation, keep-original guarantee) lives in
:func:`~anki_miner.services.subtitle_retimer.retime_subtitle`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.services.retime_reference import ReferenceOverride
from anki_miner.utils.file_pairing import resolve_output_path
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class SubtitleRetimeWorker(FileQueueWorker):
    """Retime a list of (video, subtitle) pairs.

    Per pair:
    1. Emits ``file_started(idx)``.
    2. Determines output path: ``video.stem + in_sub.suffix``, in *output_dir* if
       given, else next to the video.
    3. If the output already exists and *overwrite* is False — emits
       ``file_skipped(idx, out_sub, reason)`` and continues.
    4. Calls the retimer; forwards pipeline decision/progress lines via
       ``file_progress``.
    5. Emits ``file_finished(idx, out_sub, None)`` on success, or
       ``file_finished(idx, None, error_str)`` on failure / cancel. A pipeline
       that kept the original (every engine's result failed validation) is a
       per-pair failure whose message says the original was left untouched;
       the queue continues. On success, ``file_note(idx, line)`` also fires
       once per fact worth keeping around after the run — which engine won,
       and whether a ``.pre-retime.bak`` sibling was written (C-7/C-10).
       Deliberately separate from ``file_progress``: the base tab renders that
       one as transient status text, overwritten by the next update, so it
       cannot carry information the user needs to find after the run ends.

    Cancel is honoured between pairs and propagated into the retimer via
    ``self._cancel_event``.

    After the loop, ``queue_finished()`` is emitted unconditionally.

    Args:
        config: Frozen :class:`~anki_miner.config.AnkiMinerConfig` instance.
        pairs: Ordered list of ``(video_path, subtitle_path)`` pairs.
        output_dir: When given, output subtitles are written here instead of
            next to each source video.
        overwrite: When ``True``, existing output subtitles are regenerated.
        reference_override: Explicit user pick of what to align against;
            None auto-selects (embedded subtitle track preferred, audio
            fallback) per video.
        retimer: Optional callable with the same signature as
            :func:`~anki_miner.services.subtitle_retimer.retime_subtitle`;
            defaults to that function.  Injected by tests.
        parent: Optional parent QObject.
    """

    #: One durable fact about a just-finished file, for the Activity log —
    #: NOT for the transient status label (see the class docstring). Fired
    #: zero or more times per pair, always before that pair's ``file_finished``.
    file_note = pyqtSignal(int, str)

    def __init__(
        self,
        config,
        pairs: list[tuple[Path, Path]],
        *,
        output_dir: Path | None = None,
        overwrite: bool = False,
        reference_override: ReferenceOverride | None = None,
        retimer=None,
        parent=None,
    ) -> None:
        """Initialise the worker."""
        super().__init__(parent)
        self._config = config
        self._pairs = list(pairs)
        self._output_dir = output_dir
        self._overwrite = overwrite
        self._reference_override = reference_override

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
            self.file_skipped.emit(idx, out_sub, msg)
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

        Per-pair errors are forwarded as signals; this worker declares no
        ``_FATAL_QUEUE_EXCEPTIONS`` (the base default is an empty tuple), so
        nothing here stops the queue. A missing alass no longer qualifies —
        the self-tuning engine chain shortens itself instead, falling through
        to ffsubsync candidates; see :func:`~anki_miner.services.subtitle_retimer.retime_subtitle`.
        """
        try:
            # log_cb forwards pipeline decision/progress lines via
            # file_progress. There is no percentage — emit pct=0.
            def _log_cb(line: str) -> None:
                self.file_progress.emit(idx, 0, line)

            # Captured before the run: a successful commit always leaves
            # out_sub in place, so this is the only point that can tell
            # whether _commit() found something there to back up.
            existed_before = out_sub.exists()

            outcome = self._retimer(
                self._config,
                video,
                in_sub,
                out_sub,
                reference_override=self._reference_override,
                cancel_event=self._cancel_event,
                log_cb=_log_cb,
            )

            if outcome:
                # Transient status text only — see file_note below for the
                # durable record of the same facts.
                self.file_progress.emit(idx, 100, self.tr("Done"))

                engine = getattr(outcome, "engine", None)
                if engine:
                    self.file_note.emit(idx, tr_format(self.tr("Retimed with %1"), engine))
                if existed_before:
                    from anki_miner.services.subtitle_retimer import BACKUP_SUFFIX

                    self.file_note.emit(
                        idx,
                        tr_format(self.tr("Original backed up as %1"), out_sub.name + BACKUP_SUFFIX),
                    )
                self.file_finished.emit(idx, out_sub, None)
            elif self.is_cancelled or getattr(outcome, "cancelled", False):
                self.file_finished.emit(idx, None, self.tr("Cancelled"))
            else:
                reason = getattr(outcome, "reason", "") or self.tr("no trustworthy sync; original kept unchanged")
                self.file_finished.emit(
                    idx,
                    None,
                    tr_format(self.tr("Retiming failed for %1: %2"), video.name, reason),
                )

        except Exception as exc:  # noqa: BLE001 — per-pair isolation
            logger.exception("subtitle_retime_worker: error on %s", video)
            self.file_finished.emit(idx, None, str(exc))

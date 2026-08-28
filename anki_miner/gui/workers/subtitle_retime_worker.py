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
from anki_miner.utils.file_pairing import RETIMED_SUFFIX, resolve_output_path
from anki_miner.utils.file_utils import bounded_output_name
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class SubtitleRetimeWorker(FileQueueWorker):
    """Retime a list of (video, subtitle) pairs.

    Per pair:
    1. Emits ``file_started(idx)``.
    2. Determines output path: ``in_sub.stem + "_retimed" + in_sub.suffix``, in
       *output_dir* if given, else next to the video. The suffix is why the
       input subtitle survives a run untouched — no path this worker builds can
       collide with the file it read.
    3. If the output already exists and *overwrite* is False — emits
       ``file_skipped(idx, out_sub, reason)`` and continues.
    4. Calls the retimer; forwards pipeline decision/progress lines via
       ``file_progress``.
    5. Emits ``file_finished(idx, out_sub, None)`` on success, or
       ``file_finished(idx, None, error_str)`` on failure / cancel. A pipeline
       that kept the original (every engine's result failed validation) is a
       per-pair failure whose message says the original was left untouched;
       the queue continues. On success, ``file_note(idx, line)`` also fires
       with the winning engine — a fact worth keeping around after the run
       (C-7/C-10). Deliberately separate from ``file_progress``: the base tab
       renders that one as transient status text, overwritten by the next
       update, so it cannot carry information the user needs to find after the
       run ends.

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

        # Determine output path: the input subtitle's own stem plus _retimed,
        # keeping its extension. The suffix is what keeps the user's original
        # subtitle on disk — the output can never be the input, so nothing has
        # to be copied aside before the commit. bounded_output_name keeps a long
        # source name from pushing the derived one past NAME_MAX, and
        # resolve_output_path aims an overwrite at an existing
        # visually-identical (NFC/NFD- or case-variant) file instead of spawning
        # a Windows duplicate.
        out_dir = self._output_dir if self._output_dir is not None else video.parent
        name = bounded_output_name(in_sub.stem, RETIMED_SUFFIX + in_sub.suffix, out_dir)
        out_sub = resolve_output_path(out_dir, name)

        if out_sub.exists() and not self._overwrite:
            logger.debug("subtitle_retime_worker: skipped %s (exists)", out_sub)
            msg = self.tr("Skipped, exists")
            self.file_progress.emit(idx, 100, msg)
            self.file_skipped.emit(idx, out_sub, msg)
            return

        # Ensure output directory exists before writing.
        if self._output_dir is not None:
            self._output_dir.mkdir(parents=True, exist_ok=True)

        self._process_pair(idx, video, in_sub, out_sub)

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

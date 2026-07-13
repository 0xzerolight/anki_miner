"""Worker that condenses media files to dialogue-only audio (Audio Condenser).

The 5-signal contract and per-file queue loop live in
:class:`~anki_miner.gui.workers.file_queue_worker.FileQueueWorker`; the per-file
product policy (subtitle-source priority, JP-track pick, the pure interval math,
the ffmpeg condense pass, sidecar writing) lives in
:func:`~anki_miner.services.audio_condenser.condense_one`. This worker is the
signal adapter: it resolves the output path, runs the skip gate, calls
``condense_one``, and maps its structured :class:`~anki_miner.services.audio_condenser.CondenseStatus`
back to translated messages. ``EncoderUnavailableError`` is declared as a
queue-stopping fatal exception.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.services.audio_condenser import (
    CondenseResult,
    CondenseStatus,
    EncoderUnavailableError,
    condense_one,
)
from anki_miner.utils.file_pairing import resolve_output_path
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


@dataclass
class CondenseItem:
    """One media file queued for condensing.

    ``external_sub`` is a user-picked subtitle file (single mode); when None the
    service discovers a sibling or embedded subtitle track (D9).
    """

    media: Path
    external_sub: Path | None = None


class CondenseWorker(FileQueueWorker):
    """Condense a list of media files down to their dialogue audio.

    Per file:
    1. Emits ``file_started(idx)``.
    2. Resolves ``<stem>_condensed.<format>`` in *output_dir* (or next to the
       media). If it already exists and *overwrite* is False — emits
       ``file_skipped(idx, out_audio)`` and continues.
    3. Delegates to :func:`~anki_miner.services.audio_condenser.condense_one`,
       which resolves a subtitle source (D9 priority), runs the pipeline, invokes
       ffmpeg, and optionally writes sidecars.
    4. Maps the returned :class:`~anki_miner.services.audio_condenser.CondenseStatus`
       to a translated ``file_finished`` / ``file_progress`` message. On a
       successful audio write whose optional sidecar write failed, the warning is
       surfaced through the final progress message — never as a ``file_finished``
       error (the audio is already good).

    :class:`~anki_miner.services.audio_condenser.EncoderUnavailableError` stops
    the entire queue (every remaining file would hit the same missing encoder)
    after emitting a per-file error for the triggering file — distinct from a
    user cancel (``is_cancelled`` stays False). All other exceptions are caught
    per-file so the queue continues.

    Cancel is honoured between files and propagated into the service via
    ``self._cancel_event``. After the loop, ``queue_finished()`` is emitted
    unconditionally.

    Args:
        config: Frozen :class:`~anki_miner.config.AnkiMinerConfig` instance.
        items: Ordered list of :class:`CondenseItem`.
        output_dir: When given, condensed audio is written here instead of next
            to each source media file.
        overwrite: When ``True``, an existing condensed audio file is regenerated.
        padding_ms: Milliseconds of padding added to each cue before merging.
        offset_ms: Millisecond offset applied to every cue (once).
        output_format: ``mp3`` | ``opus`` | ``flac`` — the audio container/codec.
        bitrate_kbps: Bitrate for lossy formats (ignored by flac).
        filtered_chars: Characters whose removal empties a line (SFX/music glyphs).
        write_subs: When ``True``, condensed SRT + LRC sidecars are written.
        audio_track_override: Audio-stream index to condense; None auto-detects.
        subtitle_track_override: Embedded subtitle ``sub_index`` to extract; None
            picks the first Japanese-tagged text track, else the first text track.
        service: Optional :class:`~anki_miner.services.audio_condenser.AudioCondenserService`;
            one is built from *config* if omitted (injected by tests).
        parent: Optional parent QObject.
    """

    #: A missing encoder dooms every remaining file — stop the queue (see base loop).
    _FATAL_QUEUE_EXCEPTIONS = (EncoderUnavailableError,)

    def __init__(
        self,
        config,
        items: list[CondenseItem],
        *,
        output_dir: Path | None = None,
        overwrite: bool = False,
        padding_ms: int = 500,
        offset_ms: int = 0,
        output_format: str = "mp3",
        bitrate_kbps: int = 96,
        filtered_chars: str = "",
        write_subs: bool = False,
        audio_track_override: int | None = None,
        subtitle_track_override: int | None = None,
        service=None,
        parent=None,
    ) -> None:
        """Initialise the worker."""
        super().__init__(parent)
        self._config = config
        self._items = list(items)
        self._output_dir = output_dir
        self._overwrite = overwrite
        self._padding_ms = padding_ms
        self._offset_ms = offset_ms
        self._output_format = output_format
        self._bitrate_kbps = bitrate_kbps
        self._filtered_chars = filtered_chars
        self._write_subs = write_subs
        self._audio_track_override = audio_track_override
        self._subtitle_track_override = subtitle_track_override

        if service is None:
            from anki_miner.services.audio_condenser import AudioCondenserService

            self._service = AudioCondenserService(config)
        else:
            self._service = service

    def _queue_items(self) -> list[CondenseItem]:
        return self._items

    def _process_item(self, idx: int, item: CondenseItem) -> None:
        # Output name: <media stem>_condensed.<format>, resolved against
        # existing on-disk files so an overwrite replaces a visually-identical
        # (NFC/NFD- or case-variant) twin in place instead of spawning a
        # Windows duplicate. See resolve_output_path.
        out_dir = self._output_dir if self._output_dir is not None else item.media.parent
        out_audio = resolve_output_path(out_dir, f"{item.media.stem}_condensed.{self._output_format}")

        # Skip-if-exists keyed on the audio file only (D11).
        if out_audio.exists() and not self._overwrite:
            logger.debug("condense_worker: skipped %s (exists)", out_audio)
            self.file_progress.emit(idx, 100, self.tr("Skipped, exists"))
            self.file_skipped.emit(idx, out_audio)
            return

        if self._output_dir is not None:
            self._output_dir.mkdir(parents=True, exist_ok=True)

        self._process_file(idx, item, out_audio)

    def _process_file(self, idx: int, item: CondenseItem, out_audio: Path) -> None:
        """Run :func:`condense_one` for one file and map its result to signals.

        Per-file errors are forwarded as signals; only a ``_FATAL_QUEUE_EXCEPTIONS``
        member (encoder missing) propagates, for the base loop to stop the queue.
        """

        def _progress_cb(pct: int) -> None:
            self.file_progress.emit(idx, pct, tr_format(self.tr("Condensing: %1%"), pct))

        try:
            result = condense_one(
                self._service,
                self._config,
                item.media,
                item.external_sub,
                out_audio,
                offset_ms=self._offset_ms,
                padding_ms=self._padding_ms,
                filtered_chars=self._filtered_chars,
                bitrate_kbps=self._bitrate_kbps,
                audio_track_override=self._audio_track_override,
                subtitle_track_override=self._subtitle_track_override,
                write_subs=self._write_subs,
                progress_cb=_progress_cb,
                cancel_event=self._cancel_event,
            )
        except self._FATAL_QUEUE_EXCEPTIONS:
            # Missing encoder affects every remaining file. Re-raise so the base
            # queue loop reports this file's error and stops the queue without
            # poisoning is_cancelled (a tool error, not a user cancel).
            raise
        except Exception as exc:  # noqa: BLE001 — per-file isolation
            logger.exception("condense_worker: error on %s", item.media)
            if not self.is_cancelled:
                self.file_finished.emit(idx, None, str(exc))
            return

        self._emit_result(idx, item, result)

    def _emit_result(self, idx: int, item: CondenseItem, result: CondenseResult) -> None:
        """Map a :class:`CondenseResult` status code to translated worker signals."""
        name = item.media.name
        status = result.status

        if status is CondenseStatus.SUCCESS:
            warning = (
                tr_format(self.tr("Audio done; subtitle write failed: %1"), result.sidecar_error)
                if result.sidecar_error
                else None
            )
            self.file_progress.emit(idx, 100, warning or self.tr("Done"))
            self.file_finished.emit(idx, result.out_audio, None)
        elif status is CondenseStatus.CANCELLED:
            self.file_finished.emit(idx, None, self.tr("Cancelled"))
        elif status is CondenseStatus.NO_SOURCE:
            self.file_finished.emit(idx, None, tr_format(self.tr("No subtitle source found for %1"), name))
        elif status is CondenseStatus.SUBTITLE_TRACK_NOT_FOUND:
            self.file_finished.emit(
                idx,
                None,
                tr_format(
                    self.tr("Subtitle track %1 not found in %2"),
                    self._subtitle_track_override,
                    name,
                ),
            )
        elif status is CondenseStatus.BITMAP_ONLY:
            self.file_finished.emit(
                idx,
                None,
                tr_format(
                    self.tr("Only image-based subtitles (%1) in %2, which can't be condensed"),
                    result.codecs,
                    name,
                ),
            )
        elif status is CondenseStatus.EXTRACT_FAILED:
            self.file_finished.emit(idx, None, tr_format(self.tr("Failed to extract embedded subtitle from %1"), name))
        elif status is CondenseStatus.NO_DIALOGUE:
            self.file_finished.emit(idx, None, tr_format(self.tr("No dialogue lines found in %1"), name))
        elif status is CondenseStatus.CONDENSE_FAILED:
            self.file_finished.emit(idx, None, tr_format(self.tr("Condensing failed for %1"), name))

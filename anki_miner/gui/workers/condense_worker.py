"""Worker that condenses media files to dialogue-only audio (Audio Condenser).

Signal contract (frozen — mirrors SubtitleGenWorker / SubtitleRetimeWorker):
    ``file_started(int)``                      — emitted at the start of each file (idx)
    ``file_progress(int, int, str)``           — (idx, pct 0-100, message) during condensing
    ``file_finished(int, object, object)``     — (idx, out_path|None, error_str|None)
    ``file_skipped(int, object)``              — (idx, out_path) when output exists and overwrite is False
    ``queue_finished()``                       — emitted once after the last file
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.audio_condenser import (
    EncoderUnavailableError,
    build_periods,
    filter_lines,
    load_subtitle_events,
    map_events_to_condensed,
    shift_events,
    write_condensed_lrc,
    write_condensed_srt,
)
from anki_miner.utils.audio_track_detector import (
    JAPANESE_LANGUAGE_CODES,
    SubtitleStream,
    list_subtitle_streams,
)
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from anki_miner.utils.file_pairing import find_sibling_subtitle, resolve_output_path
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

# Subtitle-source priority for the condenser (D9). Unlike the mining default it
# includes ``.vtt`` — the condenser accepts WebVTT sidecars (D12).
_CONDENSER_SUBTITLE_PRIORITY: tuple[str, ...] = (".ass", ".ssa", ".srt", ".vtt")


@dataclass
class CondenseItem:
    """One media file queued for condensing.

    ``external_sub`` is a user-picked subtitle file (single mode); when None the
    worker discovers a sibling or embedded subtitle track (D9).
    """

    media: Path
    external_sub: Path | None = None


class CondenseWorker(CancellableWorker):
    """Condense a list of media files down to their dialogue audio.

    Per file:
    1. Emits ``file_started(idx)``.
    2. Resolves ``<stem>_condensed.<format>`` in *output_dir* (or next to the
       media). If it already exists and *overwrite* is False — emits
       ``file_skipped(idx, out_audio)`` and continues.
    3. Resolves a subtitle source (D9 priority: explicit → sibling → embedded
       text track); runs the pure pipeline (load → shift → filter → build
       periods). Zero periods (empty/all-comment sub or every line filtered) →
       ``file_finished(idx, None, reason)`` **without** invoking ffmpeg.
    4. Calls ``service.condense`` (progress forwarded via ``file_progress``).
    5. On success, optionally writes ``<stem>_condensed.srt``/``.lrc`` sidecars.
       A sidecar write failure is **non-fatal** — the audio already succeeded, so
       it is logged and surfaced as the final progress message, never turned into
       a ``file_finished`` error (the error slot stays None on audio success).
    6. Emits ``file_progress(idx, 100, …)`` + ``file_finished(idx, out_audio, None)``.

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

    #: Emitted at the start of each file; argument is the 0-based file index.
    file_started = pyqtSignal(int)
    #: (idx, pct 0-100, message) — progress within a single file.
    file_progress = pyqtSignal(int, int, str)
    #: (idx, out_path|None, error_str|None) — outcome for each file.
    file_finished = pyqtSignal(int, object, object)
    #: (idx, out_path) — emitted when the output already exists and overwrite is False.
    file_skipped = pyqtSignal(int, object)
    #: Emitted once after all files have been processed (or skipped / errored).
    queue_finished = pyqtSignal()

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
        # Set when the encoder is missing: stops the queue without poisoning
        # is_cancelled (a tool error, not a user cancel).
        self._stop_queue = False

        if service is None:
            from anki_miner.services.audio_condenser import AudioCondenserService

            self._service = AudioCondenserService(config)
        else:
            self._service = service

    def run(self) -> None:
        """Execute condensing for all files in the background thread."""
        try:
            self._process_queue()
        finally:
            self.queue_finished.emit()

    def _process_queue(self) -> None:
        for idx, item in enumerate(self._items):
            if self.is_cancelled or self._stop_queue:
                break

            self.file_started.emit(idx)

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
                continue

            if self._output_dir is not None:
                self._output_dir.mkdir(parents=True, exist_ok=True)

            self._process_file(idx, item, out_audio)

    def _process_file(self, idx: int, item: CondenseItem, out_audio: Path) -> None:
        """Process a single media file; never raises (errors forwarded as signals)."""
        temp_sub: Path | None = None
        try:
            sub_path, temp_sub, source_error = self._resolve_subtitle_source(item)
            if sub_path is None:
                self.file_finished.emit(idx, None, source_error)
                return

            events = load_subtitle_events(sub_path)
            shifted = shift_events(events, self._offset_ms)
            filtered = filter_lines(shifted, self._filtered_chars)
            periods = build_periods(filtered, self._padding_ms)

            if not periods:
                self.file_finished.emit(
                    idx,
                    None,
                    tr_format(self.tr("No dialogue lines found in %1"), item.media.name),
                )
                return

            def _progress_cb(pct: int) -> None:
                self.file_progress.emit(idx, pct, tr_format(self.tr("Condensing: %1%"), pct))

            ok = self._service.condense(
                item.media,
                periods,
                out_audio,
                audio_track_override=self._audio_track_override,
                bitrate_kbps=self._bitrate_kbps,
                progress_cb=_progress_cb,
                cancel_event=self._cancel_event,
            )

            if not ok:
                if self.is_cancelled:
                    self.file_finished.emit(idx, None, self.tr("Cancelled"))
                else:
                    self.file_finished.emit(
                        idx,
                        None,
                        tr_format(self.tr("Condensing failed for %1"), item.media.name),
                    )
                return

            # Audio written. Optional condensed subtitle sidecars are best-effort:
            # a write failure is surfaced as a warning message, never a
            # file_finished error (the audio is already good).
            warning = self._write_condensed_subs(filtered, periods, out_audio) if self._write_subs else None
            self.file_progress.emit(idx, 100, warning or self.tr("Done"))
            self.file_finished.emit(idx, out_audio, None)

        except EncoderUnavailableError as exc:
            # Missing encoder affects every remaining file — report this file's
            # failure first, then flag the queue to stop. Do NOT touch
            # _cancel_event: is_cancelled must stay False so callers can tell a
            # tool error from a user cancel.
            self.file_finished.emit(idx, None, str(exc))
            self._stop_queue = True

        except Exception as exc:  # noqa: BLE001 — per-file isolation
            logger.exception("condense_worker: error on %s", item.media)
            if not self.is_cancelled:
                self.file_finished.emit(idx, None, str(exc))
        finally:
            # Delete the extracted embedded-subtitle temp file (the service hands
            # ownership to the caller). External/sibling subs are never touched.
            if temp_sub is not None:
                with contextlib.suppress(OSError):
                    if temp_sub.exists():
                        temp_sub.unlink()

    def _resolve_subtitle_source(self, item: CondenseItem) -> tuple[Path | None, Path | None, str | None]:
        """Resolve *item*'s subtitle source (D9 priority).

        Returns ``(sub_path, temp_sub, error)``:
          * usable external / sibling / embedded sub → ``(path, temp_or_None, None)``
          * no usable source → ``(None, temp_or_None, reason_message)``

        ``temp_sub`` is the extracted embedded temp file (deleted by the caller in
        the per-file ``finally``); it is None for external and sibling subs.
        """
        # 1. Explicit user-picked file (single mode).
        if item.external_sub is not None:
            return item.external_sub, None, None

        # 2. Sibling external sub (condenser priority, incl. .vtt).
        sibling = find_sibling_subtitle(item.media, priority=_CONDENSER_SUBTITLE_PRIORITY)
        if sibling is not None:
            return sibling, None, None

        # 3. Embedded text subtitle track.
        return self._resolve_embedded_subtitle(item.media)

    def _resolve_embedded_subtitle(self, media: Path) -> tuple[Path | None, Path | None, str | None]:
        """Extract an embedded text subtitle from *media* (D9), or report why not."""
        streams = list_subtitle_streams(media, resolve_ffprobe(self._config))
        if not streams:
            return None, None, tr_format(self.tr("No subtitle source found for %1"), media.name)

        stream = self._pick_subtitle_stream(streams)
        if stream is None:
            if self._subtitle_track_override is not None:
                return (
                    None,
                    None,
                    tr_format(
                        self.tr("Subtitle track %1 not found in %2"),
                        self._subtitle_track_override,
                        media.name,
                    ),
                )
            codecs = ", ".join(sorted({s.codec_name or "unknown" for s in streams}))
            return (
                None,
                None,
                tr_format(
                    self.tr("Only image-based subtitles (%1) in %2, which can't be condensed"),
                    codecs,
                    media.name,
                ),
            )

        temp_dir = self._config.media_temp_folder
        temp_dir.mkdir(parents=True, exist_ok=True)
        extracted = self._service.extract_embedded_subtitle(media, stream, temp_dir, cancel_event=self._cancel_event)
        if extracted is None:
            if self.is_cancelled:
                return None, None, self.tr("Cancelled")
            return None, None, tr_format(self.tr("Failed to extract embedded subtitle from %1"), media.name)
        return extracted, extracted, None

    def _pick_subtitle_stream(self, streams: list[SubtitleStream]) -> SubtitleStream | None:
        """Choose a subtitle stream: override sub_index → first JP text → first text."""
        if self._subtitle_track_override is not None:
            return next((s for s in streams if s.sub_index == self._subtitle_track_override), None)
        text_streams = [s for s in streams if s.is_text]
        if not text_streams:
            return None
        return next(
            (s for s in text_streams if s.language_tag in JAPANESE_LANGUAGE_CODES),
            text_streams[0],
        )

    def _write_condensed_subs(self, filtered_events, periods, out_audio: Path) -> str | None:
        """Write condensed SRT + LRC sidecars beside *out_audio*.

        Consumes the **filtered, shifted** events (D4) so the sidecars show only
        the audible dialogue. Returns None on success, or a warning string when a
        writer fails — the audio is already written, so this is non-fatal.
        """
        try:
            condensed = map_events_to_condensed(filtered_events, periods)
            srt_path = resolve_output_path(out_audio.parent, f"{out_audio.stem}.srt")
            lrc_path = resolve_output_path(out_audio.parent, f"{out_audio.stem}.lrc")
            write_condensed_srt(condensed, srt_path)
            write_condensed_lrc(condensed, lrc_path)
            return None
        except OSError as exc:
            logger.warning("condense_worker: condensed subtitle write failed for %s: %s", out_audio, exc)
            return tr_format(self.tr("Audio done; subtitle write failed: %1"), str(exc))

"""Worker that transcribes video files to SRT subtitles using the ASR engine.

Signal contract (frozen — mirrors SubtitleRetimeWorker):
    ``file_started(int)``                      — emitted at the start of each file (idx)
    ``file_progress(int, int, str)``           — (idx, pct 0-100, message) during transcription
    ``file_finished(int, object, object)``     — (idx, out_path|None, error_str|None)
    ``file_skipped(int, object)``              — (idx, out_path) when output exists and overwrite is False
    ``queue_finished()``                       — emitted once after the last file
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


class SubtitleGenWorker(CancellableWorker):
    """Transcribe a list of video files to SRT subtitle files.

    Per file:
    1. Emits ``file_started(idx)``.
    2. If ``<stem>.srt`` already exists and *overwrite* is False — emits
       ``file_skipped(idx, existing_path)`` and continues.
    3. Extracts full audio to a temp WAV → converts to float32 → transcribes
       → writes SRT.  The temp WAV is deleted in a ``finally`` block.
    4. Emits ``file_finished(idx, out_path, None)`` on success, or
       ``file_finished(idx, None, error_str)`` on failure.

    Cancel is honored between files and propagated into the extractor /
    transcriber via ``self._cancel_event``.

    After the loop, ``queue_finished()`` is emitted unconditionally.

    Args:
        config: Frozen :class:`~anki_miner.config.AnkiMinerConfig` instance.
        video_files: Ordered list of paths to video files to transcribe.
        output_dir: When given, SRT files are written here instead of next to
            each source video.
        overwrite: When ``True``, existing SRT files are re-generated.
        extractor: Optional :class:`~anki_miner.services.media_extractor.MediaExtractorService`
            instance; one is created from *config* if omitted.
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
        video_files: list[Path],
        *,
        output_dir: Path | None = None,
        overwrite: bool = False,
        extractor=None,
        parent=None,
    ) -> None:
        """Initialise the worker."""
        super().__init__(parent)
        self._config = config
        self._video_files = list(video_files)
        self._output_dir = output_dir
        self._overwrite = overwrite

        if extractor is None:
            from anki_miner.services.media_extractor import MediaExtractorService

            self._extractor = MediaExtractorService(config)
        else:
            self._extractor = extractor

    def run(self) -> None:
        """Execute transcription for all files in the background thread."""
        try:
            self._process_queue()
        finally:
            self.queue_finished.emit()

    def _process_queue(self) -> None:
        for idx, video_path in enumerate(self._video_files):
            if self.is_cancelled:
                break

            self.file_started.emit(idx)

            # Determine output SRT path.
            if self._output_dir is not None:
                out_srt = self._output_dir / (video_path.stem + ".srt")
            else:
                out_srt = video_path.with_suffix(".srt")

            # Skip-if-exists logic.
            if out_srt.exists() and not self._overwrite:
                logger.debug("subtitle_gen_worker: skipped %s (exists)", out_srt)
                self.file_progress.emit(idx, 100, self.tr("Skipped, exists"))
                self.file_skipped.emit(idx, out_srt)
                continue

            self._process_file(idx, video_path, out_srt)

    def _process_file(self, idx: int, video_path: Path, out_srt: Path) -> None:
        """Process a single video file; never raises (errors forwarded as signals)."""
        from anki_miner.services.asr import srt_writer, transcriber
        from anki_miner.services.media_extractor import wav_to_float32

        # Use the same temp folder convention as the rest of the project.
        temp_dir = self._config.media_temp_folder
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Build a deterministic-enough temp WAV name; mkstemp gives a unique fd.
        fd, tmp_wav_str = tempfile.mkstemp(
            prefix="asr_",
            suffix=".wav",
            dir=temp_dir,
        )
        os.close(fd)
        tmp_wav = Path(tmp_wav_str)

        try:
            # --- Stage 1: extract audio ---
            self.file_progress.emit(
                idx,
                0,
                tr_format(self.tr("Extracting audio: %1"), video_path.name),
            )

            ok = self._extractor.extract_full_audio(
                video_path,
                tmp_wav,
                cancel_event=self._cancel_event,
            )

            if self.is_cancelled:
                self.file_finished.emit(idx, None, self.tr("Cancelled"))
                return

            if not ok:
                raise RuntimeError(tr_format(self.tr("Audio extraction failed for %1"), video_path.name))

            # --- Stage 2: load audio ---
            audio, sample_rate, duration_s = wav_to_float32(tmp_wav)

            if self.is_cancelled:
                self.file_finished.emit(idx, None, self.tr("Cancelled"))
                return

            # --- Stage 3: transcribe ---
            def _progress_cb(fraction: float) -> None:
                pct = min(int(fraction * 100), 100)
                self.file_progress.emit(
                    idx,
                    pct,
                    tr_format(self.tr("Transcribing: %1%"), pct),
                )

            segments = transcriber.transcribe(
                audio,
                model_name=self._config.asr_model,
                models_root=self._config.asr_models_root,
                sample_rate=sample_rate,
                duration_s=duration_s,
                cancel_event=self._cancel_event,
                progress_cb=_progress_cb,
            )

            if self.is_cancelled:
                self.file_finished.emit(idx, None, self.tr("Cancelled"))
                return

            # No recognized speech: surface it rather than writing a blank SRT
            # and reporting a clean "Done". Empty audio is already rejected by
            # extract_full_audio; this catches silence/music-only tracks.
            if not segments:
                logger.info("subtitle_gen_worker: no speech detected in %s", video_path)
                self.file_finished.emit(idx, None, tr_format(self.tr("No speech detected in %1"), video_path.name))
                return

            # --- Stage 4: write SRT ---
            if self._output_dir is not None:
                self._output_dir.mkdir(parents=True, exist_ok=True)

            srt_writer.segments_to_srt(segments, out_srt)

            # Force 100% on success.
            self.file_progress.emit(idx, 100, self.tr("Done"))
            self.file_finished.emit(idx, out_srt, None)

        except Exception as exc:  # noqa: BLE001 — per-file isolation
            logger.exception("subtitle_gen_worker: error on %s", video_path)
            error_str = str(exc)
            if not self.is_cancelled:
                self.file_finished.emit(idx, None, error_str)
        finally:
            # Always clean up the temp WAV.
            try:
                if tmp_wav.exists():
                    tmp_wav.unlink()
            except OSError:
                logger.warning("subtitle_gen_worker: could not delete temp WAV %s", tmp_wav)

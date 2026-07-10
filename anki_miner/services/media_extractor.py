"""Service for extracting media (screenshots and audio) from video files."""

import contextlib
import hashlib
import itertools
import logging
import subprocess
import threading
import wave
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces import ProgressCallback
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.utils import (
    AudioStream,
    ensure_directory,
    find_japanese_audio_stream,
    list_audio_streams,
    safe_filename,
)
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.subprocess_utils import no_window_kwargs

logger = logging.getLogger(__name__)

# Sentinel default for the threaded ``animated_format`` parameter. Distinct from
# ``None`` (which means "no usable animated encoder — skip the screenshot"):
# ``_RESOLVE`` means "argument not supplied — resolve the format myself". The
# orchestrator threads a concrete ``str | None`` down the call chain; direct
# callers (and existing tests) omit it and get the self-resolving behavior.
_RESOLVE: Any = object()


def wav_to_float32(path: Path) -> "tuple[Any, int, float]":
    """Read a mono 16-bit PCM WAV and return (samples, sample_rate, duration_s).

    The WAV produced by :meth:`MediaExtractorService.extract_full_audio` is
    always 16 kHz mono ``pcm_s16le`` (WAVE format tag 1), the only family
    Python's stdlib ``wave`` module can read. The int16 samples are scaled to
    float32 in ``[-1.0, 1.0]`` — Whisper's expected input — by dividing by
    32768.

    Memory note: the whole track is loaded at once (~115 MB/hour at 16 kHz
    float32). Fine for episodes; a multi-hour film loads ~0.5 GB. If that
    becomes a problem, pass the WAV path straight to ``WhisperModel.transcribe``
    (it decodes internally) instead of materializing the float32 array here.

    Args:
        path: Path to the WAV file written by ``extract_full_audio``.

    Returns:
        A 3-tuple of:
        - ``samples``: 1-D float32 numpy array in ``[-1.0, 1.0]``.
        - ``sample_rate``: Frame rate in Hz (typically 16 000).
        - ``duration_s``: Duration in seconds (``nframes / framerate``).
    """
    import numpy as np  # noqa: PLC0415  (intentional function-local import — numpy is an [asr] extra)

    with wave.open(str(path), "rb") as wf:
        # Fail loudly on an unexpected layout rather than silently reinterpreting
        # float/stereo bytes as garbage int16. extract_full_audio always writes
        # mono pcm_s16le; a mismatch means a stale or foreign WAV reached us.
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getcomptype() != "NONE":
            raise ValueError(
                "wav_to_float32 expects mono pcm_s16le; got "
                f"channels={wf.getnchannels()} sampwidth={wf.getsampwidth()} comptype={wf.getcomptype()}"
            )
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    # int16 → float32 in [-1.0, 1.0]. astype already yields a writable, owned
    # array (np.frombuffer over immutable bytes is read-only), which
    # faster-whisper/ctranslate2 may require — no separate .copy() needed.
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    duration = n_frames / sample_rate
    return samples, sample_rate, duration


def _kill_quietly(proc: "subprocess.Popen[str]") -> None:
    """Kill *proc*, tolerating a process that already exited.

    ``Popen.kill`` can raise ``ProcessLookupError`` (an ``OSError``) when the
    process finished and was reaped between our liveness check and the kill;
    cancellation must never crash on that race.
    """
    with contextlib.suppress(OSError):
        proc.kill()


class _FfmpegProcRegistry:
    """Live ffmpeg ``Popen`` handles for a single ``extract_media_batch`` run.

    On cancel the batch thread calls :meth:`kill_all`. Without it, in-flight
    encodes (30-60s timeouts) ran to completion and the executor's context
    exit joined them, blocking the cancelling caller for the full encode
    time. Once cancelled, :meth:`register` refuses new processes so a worker
    that was between two ffmpeg calls cannot spawn fresh work. Reaping stays
    with the owning worker thread (``communicate``/``wait``) — no zombies.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: set[subprocess.Popen[str]] = set()
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        """True once :meth:`kill_all` ran; new spawns must be skipped."""
        with self._lock:
            return self._cancelled

    def register(self, proc: "subprocess.Popen[str]") -> bool:
        """Track a live process. Returns False when already cancelled."""
        with self._lock:
            if self._cancelled:
                return False
            self._procs.add(proc)
            return True

    def unregister(self, proc: "subprocess.Popen[str]") -> None:
        with self._lock:
            self._procs.discard(proc)

    def kill_all(self) -> None:
        """Mark the run cancelled and kill every tracked process."""
        with self._lock:
            self._cancelled = True
            procs = list(self._procs)
        for proc in procs:
            _kill_quietly(proc)


class MediaExtractorService:
    """Extract screenshots and audio clips from video files (stateless service)."""

    # Seconds between cancelled_check polls while encodes are still in flight.
    _CANCEL_POLL_INTERVAL = 0.2

    def __init__(self, config: AnkiMinerConfig):
        """Initialize the media extractor.

        Args:
            config: Configuration for media extraction
        """
        self.config = config
        ensure_directory(config.media_temp_folder)
        self._audio_stream_cache: dict[Path, int | None] = {}
        self._audio_stream_list_cache: dict[Path, list[AudioStream]] = {}
        self._cache_lock = threading.Lock()
        # Lazy, cached encoder-availability probe for animated screenshots.
        # Keyed by ffmpeg encoder name (e.g. "libsvtav1", "libwebp_anim").
        self._animated_encoder_ok: dict[str, bool] = {}
        self._encoder_probe_lock = threading.Lock()
        # Per-extraction discriminator for temp clip filenames. Two words that
        # share lemma+start_time (kanji-variant collapse, or the Deck Builder's
        # dedup bypass) would otherwise map to the same {word}_{ms} name and, run
        # in parallel by extract_media_batch's ThreadPoolExecutor, race two
        # ``ffmpeg -y`` writes to one path → a corrupt clip. next() on
        # itertools.count is atomic under the GIL, so no lock is needed.
        self._clip_seq = itertools.count()

    def extract_media(
        self,
        video_file: Path,
        word: TokenizedWord,
        temp_folder: Path | None = None,
        *,
        audio_track_override: int | None = None,
        proc_registry: _FfmpegProcRegistry | None = None,
        audio_only: bool = False,
        animated_format: Any = _RESOLVE,
    ) -> MediaData:
        """Extract screenshot and audio for a single word.

        Args:
            video_file: Path to video file
            word: TokenizedWord with timing information
            temp_folder: Per-run temp directory to write output into; when
                omitted, falls back to the config-level media_temp_folder.
            audio_track_override: Optional 0-indexed audio track (audio_index) to use instead
                of auto-detecting Japanese. None (default) preserves existing JP auto-detect.
            proc_registry: Internal batch-cancel registry; extract_media_batch
                passes one so cancelled runs can kill in-flight ffmpeg.
            audio_only: When True (audiobook mining), skip screenshot extraction
                entirely; screenshot fields stay None. extract_media_batch fills
                them with per-book cover art instead.
            animated_format: Effective animated screenshot format, threaded from
                the batch so the format is resolved once per run. ``_RESOLVE``
                (the default, used by direct callers) self-resolves via
                ``resolve_animated_format``; a ``str`` is used as-is; ``None``
                means no animated encoder is available, so the animated
                screenshot is skipped (no ffmpeg spawn).

        Returns:
            MediaData with paths to extracted files
        """
        # Sanitize filename
        safe_word = safe_filename(word.lemma)
        timestamp = int(word.start_time * 1000)
        # Unique per extraction so parallel siblings sharing lemma+timestamp
        # (kanji-variant collapse / dedup bypass) never collide on one temp path.
        seq = next(self._clip_seq)

        # Effective animated format (str = encode it; None = animated unavailable).
        # _RESOLVE means "not threaded" — resolve here for direct callers/tests.
        if animated_format is _RESOLVE:
            effective_fmt = (
                self.resolve_animated_format() if (self.config.screenshot_animated and not audio_only) else None
            )
        else:
            effective_fmt = animated_format

        # The extension is the single source of the screenshot filename; it must
        # match the container _extract_animated_screenshot writes (both derive
        # from effective_fmt), or a WebP clip lands in a .avif filename.
        screenshot_ext = effective_fmt if (self.config.screenshot_animated and effective_fmt is not None) else "jpg"
        screenshot_file = f"{safe_word}_{timestamp}_{seq}.{screenshot_ext}"
        audio_file = f"{safe_word}_{timestamp}_{seq}.{self.config.audio_format}"

        output_dir = temp_folder if temp_folder is not None else self.config.media_temp_folder
        screenshot_path = output_dir / screenshot_file
        audio_path = output_dir / audio_file

        # OVH-049: two separate ffmpeg invocations each re-open the source
        # container.  Merging them into one ``-i input -map 0:v -map 0:a``
        # with two outputs would halve container-open cost, but it is
        # behavior-sensitive: the single -ss seek position is shared, so
        # the screenshot frame timing and audio window could shift relative
        # to the current per-invocation seeks (static screenshot uses
        # ``-ss before -i`` fast seek; audio uses a different start/duration
        # with padding applied).  Without a benchmark harness on real
        # multi-GB MKV sources, the container-open cost is unquantified and
        # the precision/quality risk is non-zero.  Deferred; leave as-is.

        # Extract screenshot (skipped for audiobooks — no video stream to grab).
        # When animated is configured but no encoder is available (effective_fmt
        # is None), the screenshot is skipped without spawning ffmpeg.
        screenshot_success = False
        if not audio_only and not (self.config.screenshot_animated and effective_fmt is None):
            screenshot_success = self._extract_screenshot(
                video_file, word.start_time, word.duration, screenshot_path, effective_fmt, proc_registry
            )

        # Extract audio
        audio_success = self._extract_audio(
            video_file, word.start_time, word.duration, audio_path, audio_track_override, proc_registry
        )

        return MediaData(
            screenshot_path=screenshot_path if screenshot_success else None,
            audio_path=audio_path if audio_success else None,
            screenshot_filename=screenshot_file if screenshot_success else None,
            audio_filename=audio_file if audio_success else None,
        )

    def extract_media_batch(
        self,
        video_file: Path,
        words: list[TokenizedWord],
        progress_callback: ProgressCallback | None = None,
        cancelled_check: Callable[[], bool] | None = None,
        temp_folder: Path | None = None,
        *,
        audio_track_override: int | None = None,
        audio_only: bool = False,
        animated_format: Any = _RESOLVE,
    ) -> list[tuple[TokenizedWord, MediaData]]:
        """Extract media for multiple words in parallel.

        Args:
            video_file: Path to video file
            words: List of words to extract media for
            progress_callback: Optional callback for progress reporting
            cancelled_check: Optional callable returning True when the caller
                wants in-flight work cancelled.
            temp_folder: Per-run temp directory forwarded to extract_media.
            audio_track_override: Optional 0-indexed audio track (audio_index) to use instead
                of auto-detecting Japanese. None (default) preserves existing JP auto-detect.
            audio_only: When True (audiobook mining), skip screenshots, extract
                embedded cover art once for the whole batch and share it across
                every word, and keep words on audio success instead of
                screenshot success. Missing cover art never excludes a word.
            animated_format: Effective animated screenshot format, resolved once
                per run. ``_RESOLVE`` (the default) self-resolves here before the
                pool so every worker shares one value; the orchestrator passes
                the format it already resolved (for the Activity Log) so the
                warning and the encode are always the same value.

        Returns:
            List of (word, media_data) tuples — only words whose screenshot
            succeeded (or, in audio_only mode, whose audio succeeded); the
            other medium's failure does not exclude.
        """
        if progress_callback:
            progress_callback.on_start(
                len(words),
                QCoreApplication.translate("MediaExtractorService", "Extracting media"),
            )

        # Resolve the animated screenshot format once, before the pool, so every
        # worker shares one value (no per-word re-resolution). A threaded value
        # (str | None) is used as-is; only the _RESOLVE default self-resolves.
        if animated_format is _RESOLVE:
            animated_fmt = (
                self.resolve_animated_format() if (self.config.screenshot_animated and not audio_only) else None
            )
        else:
            animated_fmt = animated_format

        media_data_list: list[tuple[TokenizedWord, MediaData]] = []
        max_workers = self.config.max_parallel_workers
        was_cancelled = False
        # Per-run registry of live ffmpeg processes so the cancel path can
        # kill them instead of waiting out their 30-60s encode timeouts.
        proc_registry = _FfmpegProcRegistry()

        # Audiobooks have no video stream, so the Picture field uses the
        # embedded cover art (attached_pic) instead — extracted once per book,
        # shared by every card. None (no cover) leaves the field blank.
        # Cover extraction runs synchronously before the polling loop, where
        # proc_registry.kill_all() cannot reach it from this thread — so honour
        # an already-set cancellation before starting it, mirroring the loop.
        if cancelled_check and cancelled_check():
            return []
        cover_path: Path | None = None
        if audio_only:
            output_dir = temp_folder if temp_folder is not None else self.config.media_temp_folder
            cover_path = self.extract_cover_art(video_file, output_dir, proc_registry=proc_registry)
        # Poll only when the caller can actually cancel; otherwise block
        # until the next future completes.
        poll = self._CANCEL_POLL_INTERVAL if cancelled_check else None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all extraction jobs
            future_to_word = {
                executor.submit(
                    self.extract_media,
                    video_file,
                    word,
                    temp_folder,
                    audio_track_override=audio_track_override,
                    proc_registry=proc_registry,
                    audio_only=audio_only,
                    animated_format=animated_fmt,
                ): word
                for word in words
            }

            # Collect results as they complete. concurrent.futures.wait with a
            # short timeout (instead of as_completed) so a cancel request is
            # noticed while encodes are still in flight, not only after one
            # of them happens to finish.
            pending = set(future_to_word)
            completed = 0
            while pending and not was_cancelled:
                done, pending = wait(pending, timeout=poll, return_when=FIRST_COMPLETED)
                if not done:
                    # Nothing finished within the poll window; only check cancel.
                    if cancelled_check and cancelled_check():
                        was_cancelled = True
                    continue
                for future in done:
                    # Check cancellation between items
                    if cancelled_check and cancelled_check():
                        was_cancelled = True
                        break

                    completed += 1
                    word = future_to_word[future]

                    try:
                        media = future.result()

                        # audio_only keys the keep/drop decision on audio (there
                        # is no per-word screenshot); default mode keeps the
                        # original screenshot-based filter.
                        keep = media.has_audio if audio_only else media.has_screenshot
                        if keep:
                            if audio_only and cover_path is not None:
                                media.screenshot_path = cover_path
                                media.screenshot_filename = cover_path.name
                            media_data_list.append((word, media))
                            if progress_callback:
                                progress_callback.on_progress(
                                    completed,
                                    tr_format(
                                        QCoreApplication.translate("MediaExtractorService", "Extracting media: %1"),
                                        word.lemma,
                                    ),
                                )
                            # OVH-044: screenshot succeeded but audio failed (default
                            # mode only).  The card is still kept — that is the
                            # intended curation policy — but the silent gap in the
                            # Audio field is surfaced to the GUI error band.
                            # audio_only mode is untouched: its keep decision already
                            # keys on has_audio, so a word reaching here always has
                            # audio.
                            if not audio_only and not media.has_audio and progress_callback:
                                progress_callback.on_error(
                                    word.lemma,
                                    QCoreApplication.translate("MediaExtractorService", "audio extraction failed"),
                                )
                        else:
                            # Full translated templates — never compose from a
                            # bare skip_reason variable (untranslatable).
                            skip_template = (
                                QCoreApplication.translate("MediaExtractorService", "No audio: %1")
                                if audio_only
                                else QCoreApplication.translate("MediaExtractorService", "No screenshot: %1")
                            )
                            if progress_callback:
                                progress_callback.on_progress(completed, tr_format(skip_template, word.lemma))
                            # OVH-043: word dropped because the primary medium
                            # failed (screenshot in default mode, audio in
                            # audio_only mode).  A frame can always be grabbed at a
                            # valid timestamp, so a screenshot miss is a real ffmpeg
                            # failure, not a clean skip.  Surface it via on_error so
                            # the GUI error band shows it; on_error is non-fatal and
                            # does not abort the run.
                            if progress_callback:
                                progress_callback.on_error(
                                    word.lemma,
                                    QCoreApplication.translate(
                                        "MediaExtractorService",
                                        "media extraction failed — see log",
                                    ),
                                )

                    except Exception as e:
                        if progress_callback:
                            progress_callback.on_error(word.lemma, str(e))

            if was_cancelled:
                # Drop queued futures, then kill in-flight ffmpeg so the
                # executor context exit (which joins workers) returns promptly.
                executor.shutdown(wait=False, cancel_futures=True)
                proc_registry.kill_all()

        if progress_callback and not was_cancelled:
            progress_callback.on_complete()

        return media_data_list

    def extract_cover_art(
        self,
        media_file: Path,
        temp_folder: Path,
        *,
        proc_registry: _FfmpegProcRegistry | None = None,
    ) -> Path | None:
        """Extract embedded cover art (attached_pic stream) from an audiobook.

        Audiobook formats (.m4b/.mp3) commonly embed the cover as an
        attached_pic video stream; ``-map 0:v:0 -frames:v 1`` pulls it out as a
        single JPEG. The filename is keyed on the source path + size so
        AnkiConnect dedups the media file across cards and runs.

        Args:
            media_file: Path to the audiobook file
            temp_folder: Directory to write the cover JPEG into
            proc_registry: Internal batch-cancel registry for killing in-flight ffmpeg.

        Returns:
            Path to the extracted cover, or None on failure / no embedded art.
        """
        try:
            size = media_file.stat().st_size
        except OSError as e:
            logger.warning("Cover art extraction skipped, cannot stat %s: %s", media_file, e)
            return None

        digest = hashlib.sha1(f"{media_file}:{size}".encode(), usedforsecurity=False).hexdigest()[:12]
        output_path = temp_folder / f"audiobook_cover_{digest}.jpg"

        cmd = [
            resolve_ffmpeg(self.config),
            "-y",
            "-i",
            str(media_file),
            "-map",
            "0:v:0",  # attached_pic is exposed as a video stream; first only —
            # the single-image muxer fails on multi-stream maps
            "-frames:v",
            "1",
            str(output_path),
        ]

        if not self._run_ffmpeg(
            cmd, "Cover art extraction", timeout=30, context=output_path.name, proc_registry=proc_registry
        ):
            return None
        return output_path if output_path.exists() else None

    def extract_full_audio(
        self,
        video_file: Path,
        out_wav: Path,
        *,
        track_override: int | None = None,
        cancel_event: "threading.Event | None" = None,
    ) -> bool:
        """Extract the full audio track from *video_file* as a 16 kHz mono 16-bit WAV.

        The output format is ``pcm_s16le`` — a raw integer PCM encoding (WAVE
        format tag 1). Float (``pcm_f32le``, tag 3) is deliberately *not* used:
        Python's stdlib ``wave`` module (which both the zero-frame guard below
        and :func:`wav_to_float32` rely on) cannot read tag-3 float WAVs and
        raises ``unknown format: 3``. 16-bit at 16 kHz is standard, ample
        Whisper input. No encoder-availability probe is performed: all ffmpeg
        builds include the ``pcm_*`` family of codecs.

        Audio stream resolution follows the same logic as :meth:`_extract_audio`:

        - When *track_override* is set, ``-map 0:<track_override>`` is used directly.
        - Otherwise :meth:`_get_japanese_audio_stream` is called (cache + ``0:a:0``
          fallback).

        A generous flat timeout ceiling is applied so long files don't time out.

        Args:
            video_file: Path to the source video or audio file.
            out_wav: Destination path for the output WAV (will be overwritten).
            track_override: Global ffprobe stream index to use; when given, JP
                auto-detect is skipped entirely.
            cancel_event: Optional :class:`threading.Event`; when set the call
                returns ``False`` immediately without spawning ffmpeg.

        Returns:
            ``True`` on success (ffmpeg exited 0 and *out_wav* exists on disk);
            ``False`` on any failure or cancellation.
        """
        if cancel_event is not None and cancel_event.is_set():
            return False

        # Resolve track_override through the same audio-index → global-index
        # helper as _extract_audio, so callers get identical semantics: the
        # integer is an *audio* index (0-indexed among audio streams), not a
        # raw ffprobe global stream index.
        global_index = self._resolve_audio_track_global_index(video_file, track_override)

        proc_registry = _FfmpegProcRegistry()

        # Wire cancel_event → kill_all so long encodes can be interrupted.
        # A done_event signals the watcher to exit on the normal success path
        # so the daemon thread doesn't block forever holding the registry.
        cancel_thread: threading.Thread | None = None
        done_event: threading.Event | None = None
        if cancel_event is not None:
            done_event = threading.Event()
            # Capture as non-optional locals so mypy and the closure agree on type.
            _ce: threading.Event = cancel_event
            _de: threading.Event = done_event

            def _watch() -> None:
                # Poll with a short timeout so the thread wakes when either
                # cancel fires OR the work finishes — without blocking forever.
                while not _ce.is_set() and not _de.is_set():
                    _ce.wait(timeout=0.05)
                if _ce.is_set() and not _de.is_set():
                    proc_registry.kill_all()

            cancel_thread = threading.Thread(target=_watch, daemon=True)
            cancel_thread.start()

        cmd = [
            resolve_ffmpeg(self.config),
            "-y",
            "-i",
            str(video_file),
        ]

        if global_index is not None:
            cmd.extend(["-map", f"0:{global_index}"])
            logger.debug("extract_full_audio: using audio stream %d", global_index)
        else:
            cmd.extend(["-map", "0:a:0"])
            logger.warning("extract_full_audio: no Japanese audio found, using first audio stream")

        cmd.extend(
            [
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ]
        )

        # Flat 30-minute ceiling. Audio-only decode + 16 kHz resample runs far
        # faster than realtime, so this comfortably covers multi-hour sources
        # (the old 300 s could time out a long film on slow I/O). A flat value
        # avoids an extra ffprobe round-trip; it is a ceiling, not a target.
        timeout = 1800

        success = self._run_ffmpeg(
            cmd,
            "Full audio extraction",
            timeout=timeout,
            context=out_wav.name,
            proc_registry=proc_registry,
        )
        # Signal the cancel-watcher thread that the work is done so it exits
        # cleanly without holding the registry open indefinitely.
        if done_event is not None:
            done_event.set()
        if not success or not out_wav.exists():
            return False

        # Guard against a zero-frame WAV: when the source has no decodable audio
        # for the mapped stream, ffmpeg can still exit 0 and write a valid but
        # empty WAV. Without this check that empty audio would transcribe to an
        # empty SRT reported to the user as a clean "Done".
        try:
            with wave.open(str(out_wav), "rb") as wf:
                if wf.getnframes() == 0:
                    logger.warning("extract_full_audio: %s has no audio frames (no audio stream?)", out_wav.name)
                    return False
        except (wave.Error, OSError) as exc:
            logger.warning("extract_full_audio: could not verify %s: %s", out_wav.name, exc)
            return False
        return True

    def _extract_screenshot(
        self,
        video_file: Path,
        start_time: float,
        duration: float,
        output_path: Path,
        animated_fmt: str | None = None,
        proc_registry: _FfmpegProcRegistry | None = None,
    ) -> bool:
        """Extract a screenshot, dispatching to the static or animated path.

        ``animated_fmt`` decides the path: a format string ("avif"/"webp")
        takes the animated path with that format; ``None`` takes the static
        JPEG path. The caller (``extract_media``) has already resolved which
        applies, so this no longer reads ``config.screenshot_animated``.
        """
        if animated_fmt is not None:
            return self._extract_animated_screenshot(
                video_file, start_time, duration, output_path, proc_registry, fmt=animated_fmt
            )
        return self._extract_static_screenshot(video_file, start_time, duration, output_path, proc_registry)

    def _run_ffmpeg(
        self,
        cmd: list[str],
        op_name: str,
        timeout: int,
        context: str = "",
        proc_registry: _FfmpegProcRegistry | None = None,
    ) -> bool:
        """Run an ffmpeg command. Log + swallow errors. Return success bool.

        Returns True only on a zero exit code. Callers may impose additional
        post-run checks (e.g. ``output_path.exists()``) on top of this.

        Spawns via ``Popen`` (not ``subprocess.run``) so a cancelled batch can
        kill in-flight encodes through *proc_registry*. The timeout semantics
        of the old ``subprocess.run`` path are preserved: on expiry the
        process is killed, reaped, and False is returned.
        """
        suffix = f" for {context}" if context else ""
        if proc_registry is not None and proc_registry.cancelled:
            return False
        try:
            # Decode ffmpeg's stderr as UTF-8 with replacement, not the platform
            # locale codec: ffmpeg echoes the (often non-ASCII Japanese) input
            # filename + stream titles to stderr, which on Windows (cp932/cp1252)
            # raises UnicodeDecodeError. Mirrors audio_track_detector._run_ffprobe_json.
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,  # detach from the TTY: a backgrounded ffmpeg reading
                # the controlling terminal gets SIGTTIN-stopped and the extraction times out.
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("%s error%s: %s", op_name, suffix, e)
            return False
        if proc_registry is not None and not proc_registry.register(proc):
            # Cancel raced the spawn: kill the fresh process; the context
            # manager exit closes its pipes and reaps it.
            with proc:
                _kill_quietly(proc)
            return False
        stderr = ""
        try:
            with proc:  # closes pipes and waits on every path — no zombies
                try:
                    _, stderr = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _kill_quietly(proc)
                    proc.communicate()  # drain pipes + reap the killed process
                    logger.warning("%s timed out%s after %ss", op_name, suffix, timeout)
                    return False
                except (subprocess.SubprocessError, OSError, ValueError) as e:
                    # ValueError covers UnicodeDecodeError from communicate()'s
                    # decode of non-ASCII ffmpeg stderr (defence-in-depth alongside
                    # errors="replace" on the Popen above).
                    _kill_quietly(proc)
                    logger.warning("%s error%s: %s", op_name, suffix, e)
                    return False
        finally:
            if proc_registry is not None:
                proc_registry.unregister(proc)
        if proc.returncode == 0:
            return True
        if proc_registry is not None and proc_registry.cancelled:
            # Killed by a batch cancel — expected, not an ffmpeg failure.
            logger.debug("%s cancelled%s", op_name, suffix)
            return False
        logger.warning("%s failed%s: ffmpeg exit code %s: %s", op_name, suffix, proc.returncode, stderr)
        return False

    def _extract_static_screenshot(
        self,
        video_file: Path,
        start_time: float,
        duration: float,
        output_path: Path,
        proc_registry: _FfmpegProcRegistry | None = None,
    ) -> bool:
        """Extract a single still frame as JPEG."""
        # Calculate screenshot time (offset from start)
        screenshot_time = start_time + min(self.config.screenshot_offset, duration / 2)

        cmd = [
            resolve_ffmpeg(self.config),
            "-y",  # Overwrite output
            "-ss",
            str(screenshot_time),
            "-i",
            str(video_file),
            "-frames:v",
            "1",  # Extract single frame
            "-q:v",
            "2",  # Quality (2 = high)
            str(output_path),
        ]

        if not self._run_ffmpeg(
            cmd, "Static screenshot extraction", timeout=30, context=output_path.name, proc_registry=proc_registry
        ):
            return False
        return output_path.exists()

    @staticmethod
    def _quality_to_avif_crf(quality: int) -> int:
        """Map user-facing 0-100 quality (higher = better) to AVIF CRF 0-63 (lower = better)."""
        clamped = max(0, min(100, int(quality)))
        return round(63 - (clamped / 100) * 63)

    @staticmethod
    def _encoder_for_format(fmt: str) -> str:
        """Return the ffmpeg encoder name for an animated format."""
        if fmt == "avif":
            return "libsvtav1"
        if fmt == "webp":
            return "libwebp_anim"
        raise ValueError(f"Unsupported animated screenshot format: {fmt}")

    def _check_encoder_available(self, encoder: str) -> bool:
        """Probe ffmpeg once for an encoder; cache result.

        Animated screenshots are opt-in via ``config.screenshot_animated`` and
        need specific ffmpeg encoders: AVIF needs a build with ``libsvtav1``,
        WebP needs ``libwebp_anim``. Distro ffmpeg packages vary, so this probes
        once and caches; a missing encoder logs a clear error and returns False
        rather than silently producing broken files.
        """
        with self._encoder_probe_lock:
            cached = self._animated_encoder_ok.get(encoder)
            if cached is not None:
                return cached
            try:
                proc = subprocess.run(
                    [resolve_ffmpeg(self.config), "-hide_banner", "-encoders"],
                    capture_output=True,
                    timeout=15,
                    text=True,
                    **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
                )
                available = proc.returncode == 0 and encoder in proc.stdout
            except (subprocess.SubprocessError, OSError) as e:
                logger.warning(f"ffmpeg encoder probe failed: {e}")
                available = False
            self._animated_encoder_ok[encoder] = available
            if not available:
                logger.error(
                    f"ffmpeg encoder '{encoder}' not available. "
                    "Animated screenshots in this format will fail. "
                    "Install ffmpeg with the required encoder, or switch format in Settings."
                )
            return available

    def resolve_animated_format(self) -> str | None:
        """Effective animated screenshot format usable on this ffmpeg build.

        Returns the configured format when its encoder is present; ``"webp"``
        when the configured format is AVIF but ``libsvtav1`` is missing and
        ``libwebp_anim`` is available (the AVIF -> WebP fallback that lets the
        SVT-AV1-less macOS Intel bundle still produce animated screenshots);
        or ``None`` when no animated encoder is available at all.

        An unknown/unsupported configured format is returned unchanged (no
        fallback) — that is a config error, handled downstream exactly as
        before (the word is skipped without spawning ffmpeg). Pure query;
        encoder probes are cached via ``_check_encoder_available`` (its lock
        makes this thread-safe), so it is cheap to call more than once.
        """
        configured = self.config.screenshot_animated_format
        try:
            encoder = self._encoder_for_format(configured)
        except ValueError:
            return configured  # unsupported format: leave it to the existing skip path
        if self._check_encoder_available(encoder):
            return configured
        if configured == "avif" and self._check_encoder_available("libwebp_anim"):
            return "webp"
        return None

    def _extract_animated_screenshot(
        self,
        video_file: Path,
        start_time: float,
        duration: float,
        output_path: Path,
        proc_registry: _FfmpegProcRegistry | None = None,
        *,
        fmt: str | None = None,
    ) -> bool:
        """Extract a short animated clip (AVIF or WebP) instead of a static frame.

        ``fmt`` is the resolved format to encode (the AVIF -> WebP fallback is
        applied upstream by ``resolve_animated_format``); ``None`` falls back to
        ``config.screenshot_animated_format`` for direct callers. The encoder,
        container, and the caller's output filename all derive from this one
        value, so they cannot disagree.
        """
        fmt = self.config.screenshot_animated_format if fmt is None else fmt
        try:
            encoder = self._encoder_for_format(fmt)
        except ValueError as e:
            logger.error(str(e))
            return False

        if not self._check_encoder_available(encoder):
            return False

        # Clip timing:
        # - When `screenshot_animated_match_audio` is enabled, the clip spans the
        #   full audio range (subtitle window + audio padding on both sides) so the
        #   visual matches the audio exactly.
        # - Otherwise, clip duration is capped by subtitle duration and configurable.
        # In both cases a 0.5s floor avoids 0-frame clips on very short subtitles.
        if self.config.screenshot_animated_match_audio:
            pad = float(self.config.audio_padding)
            clip_start = max(0.0, start_time - pad)
            clip_duration = max(duration + 2 * pad, 0.5)
        else:
            clip_start = start_time
            configured = float(self.config.screenshot_animated_clip_duration)
            clip_duration = min(configured, max(duration, 0.5))

        fps = int(self.config.screenshot_animated_fps)
        height = int(self.config.screenshot_animated_height)
        quality = int(self.config.screenshot_animated_quality)

        cmd: list[str] = [
            resolve_ffmpeg(self.config),
            "-y",
            "-ss",
            str(clip_start),
            "-t",
            str(clip_duration),
            "-i",
            str(video_file),
            "-an",
            "-sn",
            "-vf",
            f"fps={fps},scale=-2:{height}",
        ]

        if fmt == "avif":
            crf = self._quality_to_avif_crf(quality)
            cmd.extend(
                [
                    "-c:v",
                    "libsvtav1",
                    "-crf",
                    str(crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-loop",
                    "0",
                ]
            )
        else:  # webp
            cmd.extend(
                [
                    "-c:v",
                    "libwebp_anim",
                    "-quality",
                    str(max(0, min(100, quality))),
                    "-loop",
                    "0",
                ]
            )

        cmd.append(str(output_path))

        if not self._run_ffmpeg(
            cmd, "Animated screenshot extraction", timeout=60, context=output_path.name, proc_registry=proc_registry
        ):
            return False
        return output_path.exists()

    def _get_japanese_audio_stream(self, video_file: Path) -> int | None:
        """Detect Japanese audio stream index using ffprobe.

        Returns the global ffprobe stream index for ffmpeg `-map 0:N`.
        Thread-safe cache avoids re-probing the same file.
        """
        with self._cache_lock:
            if video_file in self._audio_stream_cache:
                return self._audio_stream_cache[video_file]

        result = find_japanese_audio_stream(video_file, ffprobe_cmd=resolve_ffprobe(self.config))
        global_index = result.global_index if result is not None else None

        with self._cache_lock:
            self._audio_stream_cache[video_file] = global_index
        return global_index

    def invalidate_audio_stream_cache(self, video_file: Path | None = None) -> None:
        """Clear the per-file audio stream cache.

        Pass a specific path to clear one entry; pass ``None`` to clear all.
        The orchestrator calls this at the start of each ``process_episode``
        run so cross-run file replacement (re-encode, swap, restore from
        backup) cannot strand the resolver on stale ffprobe output. Within a
        single run the cache still protects against double-probes.
        """
        with self._cache_lock:
            if video_file is None:
                self._audio_stream_list_cache.clear()
                self._audio_stream_cache.clear()
            else:
                self._audio_stream_list_cache.pop(video_file, None)
                self._audio_stream_cache.pop(video_file, None)

    def _list_audio_streams_cached(self, video_file: Path) -> list[AudioStream]:
        """Return full audio stream list for *video_file*, probing once and caching.

        Thread-safe under ``_cache_lock``.
        """
        with self._cache_lock:
            if video_file in self._audio_stream_list_cache:
                return self._audio_stream_list_cache[video_file]

        streams = list_audio_streams(video_file, ffprobe_cmd=resolve_ffprobe(self.config))

        with self._cache_lock:
            self._audio_stream_list_cache[video_file] = streams
        return streams

    def _resolve_audio_track_global_index(self, video_file: Path, audio_track_override: int | None) -> int | None:
        """Translate an optional *audio_track_override* (audio_index) to a ffprobe global index.

        - If *audio_track_override* is ``None``, returns the JP auto-detect result.
        - Otherwise looks up the stream with matching ``audio_index`` in the cached stream list
          and returns its ``global_index``. Falls back to JP auto-detect (with a warning) when
          no stream matches.
        """
        if audio_track_override is None:
            return self._get_japanese_audio_stream(video_file)

        streams = self._list_audio_streams_cached(video_file)
        for stream in streams:
            if stream.audio_index == audio_track_override:
                return stream.global_index

        logger.warning(
            "audio_track_override=%d not found in stream list (got %d streams); "
            "falling back to Japanese auto-detect",
            audio_track_override,
            len(streams),
        )
        # Reuse the streams list we already probed; don't re-run ffprobe.
        for stream in streams:
            if stream.language_tag in JAPANESE_LANGUAGE_CODES:
                return stream.global_index
        return None

    def _extract_audio(
        self,
        video_file: Path,
        start_time: float,
        duration: float,
        output_path: Path,
        audio_track_override: int | None = None,
        proc_registry: _FfmpegProcRegistry | None = None,
    ) -> bool:
        """Extract audio clip from video, preferring Japanese audio.

        Args:
            video_file: Path to video file
            start_time: Start time in seconds
            duration: Duration in seconds
            output_path: Output path for audio
            audio_track_override: Optional 0-indexed audio track (audio_index) to use instead
                of auto-detecting Japanese. None (default) preserves existing JP auto-detect.
            proc_registry: Internal batch-cancel registry for killing in-flight ffmpeg.

        Returns:
            True if successful, False otherwise
        """
        # Calculate audio timing with padding
        audio_start = max(0, start_time - self.config.audio_padding)
        audio_duration = duration + (self.config.audio_padding * 2)

        # Resolve encoder for the configured format and probe ffmpeg for support
        # before launching the encode. Cached probe; failure logs a clear error.
        encoder = "libopus" if self.config.audio_format == "opus" else "libmp3lame"
        if not self._check_encoder_available(encoder):
            return False

        # Resolve audio stream: honour override when set, else JP auto-detect.
        global_index = self._resolve_audio_track_global_index(video_file, audio_track_override)

        # Build ffmpeg command
        cmd = [
            resolve_ffmpeg(self.config),
            "-y",
            "-ss",
            str(audio_start),
            "-t",
            str(audio_duration),
            "-i",
            str(video_file),
        ]

        if global_index is not None:
            cmd.extend(["-map", f"0:{global_index}"])
            logger.debug(f"Using audio stream {global_index}")
        else:
            cmd.extend(["-map", "0:a:0"])  # First audio stream
            logger.warning("No Japanese audio found, using first audio stream")

        cmd.extend(
            [
                "-vn",  # No video
                "-acodec",
                encoder,
                "-b:a",
                f"{self.config.audio_bitrate}k",
            ]
        )

        # libopus rejects multi-channel input (e.g. 5.1 surround eac3 common in
        # anime BD/WEB-DL releases) without an explicit channel mapping. Downmix
        # to stereo — Anki flashcards play through headphones/laptop speakers,
        # surround serves no purpose. MP3 (libmp3lame) tolerates 5.1 natively so
        # we leave its channel layout alone to preserve existing behavior.
        if self.config.audio_format == "opus":
            cmd.extend(["-ac", "2"])

        cmd.append(str(output_path))

        if not self._run_ffmpeg(
            cmd, "Audio extraction", timeout=30, context=output_path.name, proc_registry=proc_registry
        ):
            return False
        return output_path.exists()

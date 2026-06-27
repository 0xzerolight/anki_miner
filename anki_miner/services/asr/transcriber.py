"""ASR transcription — convert audio to timed text segments.

No top-level numpy or faster-whisper imports. numpy is a transitive dependency
of faster-whisper so a function-local ``import numpy`` is fine in Wave B
bodies, but this skeleton must be importable without either package installed.
"""

from __future__ import annotations

import glob
import importlib.util
import itertools
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable

from anki_miner.services.asr import _engine

logger = logging.getLogger(__name__)

# Sentinel for "first-segment peek exhausted the iterator" (empty transcript) so
# an empty result is not confused with a falsy real segment.
_PEEK_EMPTY = object()

# Junk-segment drop thresholds. These are Whisper's own internal fallback gates
# (compression_ratio_threshold=2.4, log_prob_threshold=-1.0), reused here as hard
# drops on the emitted segments — they remove the residual hallucinations VAD and
# the decode flags don't catch: degenerate repetition loops (あら あら …, high
# compression ratio) and low-confidence token salad (English garbage, very low
# avg_logprob).
_MAX_COMPRESSION_RATIO = 2.4
_MIN_AVG_LOGPROB = -1.0


def _ensure_onnx_pack_on_syspath(onnx_pack_root: Path | None) -> None:
    """Add a downloaded onnxruntime pack dir to ``sys.path`` so VAD can import it.

    The in-app VAD pack extracts the full ``onnxruntime/`` package tree into
    ``onnx_pack_root``. The PyInstaller bundle excludes onnxruntime, so making
    that extracted copy importable means putting its parent dir on ``sys.path``
    before ``faster_whisper.vad`` does its lazy ``import onnxruntime``.

    Idempotent and best-effort: only acts when the dir actually holds an
    ``onnxruntime/`` package and is not already on the path. Never raises.
    """
    if onnx_pack_root is None:
        return
    try:
        if not (onnx_pack_root / "onnxruntime" / "__init__.py").exists():
            return
        root = str(onnx_pack_root)
        if root not in sys.path:
            # Append (not insert-at-0): the pack dir holds only the onnxruntime
            # tree, so it never needs to win priority, and appending means it
            # cannot shadow any same-named module already on the path.
            sys.path.append(root)
            importlib.invalidate_caches()
    except Exception:  # noqa: BLE001  (best-effort; a path problem must not abort)
        pass


def vad_available(onnx_pack_root: Path | None = None) -> bool:
    """Return True when Silero VAD can run, i.e. onnxruntime is importable.

    onnxruntime is a hard dependency of faster-whisper, so a pip ``[asr]`` install
    always has it. The PyInstaller bundle strips it (~57 MB) and ships the VAD as
    an optional in-app download pack instead; when that pack is present in
    *onnx_pack_root* this injects it onto ``sys.path`` first. ``find_spec`` is used
    so no actual import (or onnxruntime init) happens here.
    """
    if importlib.util.find_spec("onnxruntime") is not None:
        return True
    _ensure_onnx_pack_on_syspath(onnx_pack_root)
    return importlib.util.find_spec("onnxruntime") is not None


def _is_junk_segment(seg) -> bool:
    """Return True for a likely-hallucinated / non-speech segment to drop.

    ``getattr`` defaults make this a no-op for segment objects lacking the fields
    (e.g. minimal test fakes), so only real faster-whisper segments are filtered.
    A present-but-``None`` field is treated as "unknown" (not junk on that axis)
    rather than crashing the comparison.
    """
    compression_ratio = getattr(seg, "compression_ratio", 0.0)
    if compression_ratio is not None and compression_ratio > _MAX_COMPRESSION_RATIO:
        return True
    avg_logprob = getattr(seg, "avg_logprob", 0.0)
    return avg_logprob is not None and avg_logprob < _MIN_AVG_LOGPROB


def _preload_cuda_libs(cuda_libs_root: Path | None) -> None:
    """Best-effort dlopen of the cuDNN + cuBLAS shared libs into the global namespace.

    ctranslate2 dlopens these lazily at ``WhisperModel(device="cuda")`` construction.
    Loading them here with ``RTLD_GLOBAL`` lets that later dlopen resolve them WITHOUT
    the user touching ``LD_LIBRARY_PATH``. Two sources are tried, in order:

    1. The managed pack dir (``cuda_libs_root``), if provided — the in-app download
       target (a later task populates it).
    2. The ``nvidia-cudnn-cu12`` / ``nvidia-cublas-cu12`` pip packages (the
       ``[asr-cuda]`` extra), if importable.

    This helper NEVER raises: any failure (missing dir, ImportError, OSError, no libs)
    is swallowed. If nothing can be preloaded, the CUDA build attempt simply fails and
    the caller falls back to CPU.
    """
    import ctypes  # noqa: PLC0415  (function-local: module stays importable without CUDA)

    # RTLD_GLOBAL is POSIX-only; on Windows CDLL takes the default mode.
    mode = getattr(ctypes, "RTLD_GLOBAL", 0)

    def _load(path: str) -> None:
        try:
            if hasattr(ctypes, "RTLD_GLOBAL"):
                ctypes.CDLL(path, mode=mode)
            else:
                ctypes.CDLL(path)
        except Exception:  # noqa: BLE001  (best-effort; a single bad lib must not abort)
            pass

    # --- Source 1: managed pack dir ---
    if cuda_libs_root is not None:
        try:
            root = str(cuda_libs_root)
            patterns = (
                os.path.join(root, "cudnn", "**", "libcudnn*.so*"),
                os.path.join(root, "cublas", "**", "libcublas*.so*"),
                os.path.join(root, "**", "cudnn*.dll"),
                os.path.join(root, "**", "cublas*.dll"),
            )
            for pattern in patterns:
                for match in glob.glob(pattern, recursive=True):
                    _load(match)
        except Exception:  # noqa: BLE001
            pass

    # --- Source 2: pip packages (nvidia-cudnn-cu12 / nvidia-cublas-cu12) ---
    for pkg_name, lib_glob, dll_glob in (
        ("nvidia.cudnn", "libcudnn*.so*", "cudnn*.dll"),
        ("nvidia.cublas", "libcublas*.so*", "cublas*.dll"),
    ):
        try:
            import importlib  # noqa: PLC0415

            pkg = importlib.import_module(pkg_name)
            pkg_dir = Path(pkg.__file__).parent if pkg.__file__ else None
            if pkg_dir is None:
                continue
            # Linux libs live in <pkg>/lib, Windows DLLs in <pkg>/bin.
            for subdir, file_glob in (("lib", lib_glob), ("bin", dll_glob)):
                for match in glob.glob(str(pkg_dir / subdir / file_glob)):
                    _load(match)
        except Exception:  # noqa: BLE001
            pass


def _cuda_device_count() -> int:
    """Return ctranslate2's reported CUDA device count, or 0 on any failure."""
    try:
        import ctranslate2  # noqa: PLC0415  (function-local: stays importable without backend)

        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001  (no backend / driver error → treat as no GPU)
        return 0


def _resolve_model(
    requested_device: str,
    cuda_libs_root: Path | None,
    whisper_model_cls,
    model_name: str,
    models_root: Path,
    cpu_threads: int,
) -> tuple[Any, str]:
    """Construct a WhisperModel honouring *requested_device* with a CPU fallback.

    ``cpu`` builds CPU directly. ``auto``/``cuda`` build on GPU when one is present
    and construction succeeds; on no GPU or ANY construction failure they fall back
    to a CPU build (the always-safe path) — a GPU problem must never crash a run.

    Returns ``(model, device_used)`` where ``device_used`` is ``"cpu"`` or
    ``"cuda"``. The caller uses that flag to force the first decode under CUDA so
    a *deferred* GPU runtime failure (ctranslate2 validates compute-type/cuDNN
    lazily on first inference) can still fall back to CPU.
    """

    def _build_cpu():
        return whisper_model_cls(
            model_name,
            device="cpu",
            compute_type="int8",
            cpu_threads=cpu_threads,
            download_root=models_root,
            local_files_only=True,
        )

    if requested_device == "cpu":
        return _build_cpu(), "cpu"

    # requested_device in {"auto", "cuda"}
    if _cuda_device_count() <= 0:
        if requested_device == "cuda":
            logger.warning("ASR: device='cuda' requested but no CUDA GPU is available; falling back to CPU.")
        else:
            logger.info("ASR: no CUDA GPU available; using CPU.")
        return _build_cpu(), "cpu"

    _preload_cuda_libs(cuda_libs_root)
    try:
        model = whisper_model_cls(
            model_name,
            device="cuda",
            compute_type="float16",
            download_root=models_root,
            local_files_only=True,
        )
        return model, "cuda"
    except Exception as exc:  # noqa: BLE001  (CUDA libs may be missing/incompatible)
        if requested_device == "cuda":
            logger.warning(
                "ASR: device='cuda' requested but CUDA initialisation failed (%s); "
                "falling back to CPU. Ensure cuDNN/cuBLAS are installed.",
                exc,
            )
        else:
            logger.info("ASR: CUDA unavailable (%s); using CPU.", exc)
        return _build_cpu(), "cpu"


def transcribe(
    audio,  # np.ndarray float32, mono 16 kHz — typed as Any to avoid top-level numpy import
    *,
    model_name: str,
    models_root: Path,
    sample_rate: int,
    duration_s: float,
    cancel_event=None,
    progress_cb: Callable[[float], None] | None = None,
    device: str = "auto",
    cuda_libs_root: Path | None = None,
    onnx_pack_root: Path | None = None,
) -> list[tuple[float, float, str]]:
    """Transcribe *audio* using the specified faster-whisper model.

    Args:
        audio: Raw audio as a float32 numpy array, mono, sampled at *sample_rate* Hz.
        model_name: Model identifier (see ``model_manager.KNOWN_MODELS``).
        models_root: Directory containing downloaded model weights.
        sample_rate: Sampling rate of *audio* in Hz (typically 16 000).
        duration_s: Total duration of the audio in seconds; used for progress
            normalisation.
        cancel_event: Optional ``threading.Event``; transcription is aborted
            cooperatively when set.
        progress_cb: Optional callback receiving a float in ``[0.0, 1.0]``
            representing transcription progress.
        device: ``"auto"`` (GPU if usable, else CPU), ``"cuda"`` (force GPU,
            fall back to CPU if unavailable), or ``"cpu"``. A GPU problem never
            crashes a run — CPU is the always-safe fallback.
        cuda_libs_root: Optional managed dir holding downloaded cuDNN/cuBLAS
            shared libs to preload before a CUDA build.
        onnx_pack_root: Optional managed dir holding a downloaded onnxruntime
            pack; enables Silero VAD in the bundle (where onnxruntime is
            stripped) by making it importable. Ignored when onnxruntime is
            already available (pip ``[asr]`` install).

    Returns:
        A list of ``(start_s, end_s, text)`` tuples in chronological order.
    """
    # sample_rate is part of the public interface for callers that need it
    # (e.g. resampling before passing the array); faster-whisper infers it
    # from the audio array's shape directly.
    del sample_rate

    if cancel_event is not None and cancel_event.is_set():
        if progress_cb is not None:
            progress_cb(1.0)
        return []

    whisper_model_cls = _engine.get_whisper_model_cls()
    cpu_threads = min(4, os.cpu_count() or 4)
    model, device_used = _resolve_model(
        device,
        cuda_libs_root,
        whisper_model_cls,
        model_name,
        models_root,
        cpu_threads,
    )

    # Decode flags that suppress the classic large-model hallucination failures:
    #  * condition_on_previous_text=False — a hallucinated phrase no longer seeds
    #    the next window, killing runaway loops (あら×22, 何を×14).
    #  * word_timestamps=True — unlocks hallucination_silence_threshold AND snaps
    #    each segment's start/end onto real word boundaries (so subtitles no
    #    longer start before the speech).
    #  * hallucination_silence_threshold — skips long silent gaps; works even
    #    without VAD (the bundle path).
    #  * vad_filter — Silero VAD is the definitive silence remover but needs
    #    onnxruntime, which the bundle strips. It is auto-enabled when onnxruntime
    #    is importable: from a pip [asr] install, or from the downloaded VAD pack
    #    placed on sys.path by vad_available(). Off → the flags above still apply.
    use_vad = vad_available(onnx_pack_root)
    transcribe_kwargs = {
        "language": "ja",
        "condition_on_previous_text": False,
        "word_timestamps": True,
        "hallucination_silence_threshold": 2.0,
        "vad_filter": use_vad,
    }
    segments_iter, _info = model.transcribe(audio, **transcribe_kwargs)

    # ctranslate2 validates the CUDA compute-type / cuDNN kernels lazily — a model
    # that constructs cleanly on GPU can still raise when the first segment is
    # decoded. Force that first pull here; on a CUDA runtime failure rebuild on CPU
    # (the always-safe path) and restart so a GPU problem never aborts the run
    # mid-stream. The peeked segment is chained back so none is lost.
    if device_used == "cuda":
        try:
            first = next(segments_iter, _PEEK_EMPTY)
        except Exception as exc:  # noqa: BLE001  (deferred CUDA runtime failure)
            logger.warning("ASR: CUDA inference failed (%s); falling back to CPU.", exc)
            model, _ = _resolve_model("cpu", cuda_libs_root, whisper_model_cls, model_name, models_root, cpu_threads)
            segments_iter, _info = model.transcribe(audio, **transcribe_kwargs)
            first = next(segments_iter, _PEEK_EMPTY)
        if first is not _PEEK_EMPTY:
            segments_iter = itertools.chain((first,), segments_iter)

    results: list[tuple[float, float, str]] = []
    for seg in segments_iter:
        if cancel_event is not None and cancel_event.is_set():
            break
        if not _is_junk_segment(seg):
            results.append((seg.start, seg.end, seg.text.strip()))
        if progress_cb is not None and duration_s > 0:
            progress_cb(min(seg.end / duration_s, 1.0))

    if progress_cb is not None:
        progress_cb(1.0)

    return results

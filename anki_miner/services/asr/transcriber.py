"""ASR transcription — convert audio to timed text segments.

No top-level numpy or faster-whisper imports. numpy is a transitive dependency
of faster-whisper so a function-local ``import numpy`` is fine in Wave B
bodies, but this skeleton must be importable without either package installed.
"""

from __future__ import annotations

import glob
import logging
import os
from pathlib import Path
from typing import Callable

from anki_miner.services.asr import _engine

logger = logging.getLogger(__name__)


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
):
    """Construct a WhisperModel honouring *requested_device* with a CPU fallback.

    ``cpu`` builds CPU directly. ``auto``/``cuda`` build on GPU when one is present
    and construction succeeds; on no GPU or ANY construction failure they fall back
    to a CPU build (the always-safe path) — a GPU problem must never crash a run.
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
        return _build_cpu()

    # requested_device in {"auto", "cuda"}
    if _cuda_device_count() <= 0:
        if requested_device == "cuda":
            logger.warning("ASR: device='cuda' requested but no CUDA GPU is available; falling back to CPU.")
        else:
            logger.info("ASR: no CUDA GPU available; using CPU.")
        return _build_cpu()

    _preload_cuda_libs(cuda_libs_root)
    try:
        return whisper_model_cls(
            model_name,
            device="cuda",
            compute_type="float16",
            download_root=models_root,
            local_files_only=True,
        )
    except Exception as exc:  # noqa: BLE001  (CUDA libs may be missing/incompatible)
        if requested_device == "cuda":
            logger.warning(
                "ASR: device='cuda' requested but CUDA initialisation failed (%s); "
                "falling back to CPU. Ensure cuDNN/cuBLAS are installed.",
                exc,
            )
        else:
            logger.info("ASR: CUDA unavailable (%s); using CPU.", exc)
        return _build_cpu()


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
    model = _resolve_model(
        device,
        cuda_libs_root,
        whisper_model_cls,
        model_name,
        models_root,
        cpu_threads,
    )

    # vad_filter stays False on purpose: Silero VAD needs onnxruntime, which the
    # PyInstaller bundle deliberately excludes (anki_miner.spec) to save ~100 MB.
    # Enabling it here would crash only in the bundled build. Trade-off: on long
    # silence/music stretches Whisper can hallucinate repeated text.
    segments_iter, _info = model.transcribe(audio, language="ja", vad_filter=False)

    results: list[tuple[float, float, str]] = []
    for seg in segments_iter:
        if cancel_event is not None and cancel_event.is_set():
            break
        results.append((seg.start, seg.end, seg.text.strip()))
        if progress_cb is not None and duration_s > 0:
            progress_cb(min(seg.end / duration_s, 1.0))

    if progress_cb is not None:
        progress_cb(1.0)

    return results

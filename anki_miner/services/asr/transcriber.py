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
import math
import os
import sys
import types
from pathlib import Path
from typing import Any, Callable

from anki_miner.services.asr import _engine, ggml_model_installer

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

# whisper.cpp Segments expose only a single geometric-mean token probability in
# [0, 1] (with extract_probability=True), not compression_ratio/avg_logprob, so
# _is_junk_segment is a no-op on them. This floor is the cpp path's lone junk
# drop: a CONSERVATIVE cut that only removes egregious low-confidence garbage
# (think English token salad / dead-air hallucinations), never marginal speech.
# NaN/None probabilities are treated as "unknown" and kept.
# PROVISIONAL: confirmed/tuned against the manual JP-clip release gate.
_MIN_CPP_SEGMENT_PROBABILITY = 0.2

# --- Non-speech drop, CT2 path (vad_filter is OFF; see _transcribe_ct2) ---
# Whisper transcribes the whole timeline, so it hallucinates over non-speech:
# YouTube-outro phrases ("ご視聴ありがとうございました"), breaths/grunts, degenerate
# repetition loops, sung ED/OP lyrics, crowd chants. We drop a segment when an
# INDEPENDENT Silero-VAD speech mask says it lies outside speech AND a second
# junk signal agrees — so a real line the VAD merely MISSED is never deleted on
# the VAD verdict alone (the cardinal rule: never drop real dialogue).
#
# SAMPLE_RATE for the mask: faster-whisper fixes audio at 16 kHz, so get_speech_
# timestamps' sample-indexed regions convert to seconds by ÷ this.
_ASR_SAMPLE_RATE = 16000
# no_speech_prob at/above this = confident non-speech ANYWHERE (drops loud
# hallucinations that sit inside a VAD false-positive region). Real dialogue
# measured ≤0.35 across the manual JP-clip gate, so this never touches speech.
# nsp is a per-30s-WINDOW score, NOT per-segment, so it is used ONLY as this
# high-confidence solo cut and NEVER as the primary discriminator.
_CONFIDENT_NONSPEECH_PROB = 0.60
# A segment whose own time span is covered by the speech mask below this fraction
# lies OUTSIDE detected speech. Non-speech measured at exactly 0% here; real
# dialogue ≥10% (segments slightly overrun the tight, un-padded mask).
_MIN_SPEECH_OVERLAP = 0.05
# Out-of-speech segments are dropped only with a corroborating junk signal:
# a repetition loop (compression_ratio) or a long sustained span — sung lyrics
# run 7–12 s, whereas a real line the VAD missed is a short interjection (a real
# ≥ this-many-seconds line is always DETECTED by the VAD, so long+out-of-speech
# is safely non-speech). This pair, not nsp, drops songs — keeping any short
# out-of-speech real utterance regardless of its (unreliable, per-window) nsp.
_NONSPEECH_MIN_DURATION_S = 4.0
# PROVISIONAL: tuned against the manual JP-clip release gate (anime; verify on
# dense-BGM / quiet-VO before trusting on other genres).


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


def _speech_mask(audio, onnx_pack_root: Path | None):
    """Return Silero-VAD speech regions as ``[(start_s, end_s), ...]`` or None.

    Runs faster-whisper's bundled Silero VAD once over *audio* to locate speech.
    Returns None when the VAD cannot run (onnxruntime absent — the slim bundle
    without the downloaded pack), in which case the caller skips the overlap-based
    non-speech drop and only the confidence gates apply (documented degradation:
    sung lyrics / faint non-speech may leak). Never raises.

    ``get_speech_timestamps`` returns SAMPLE indices; they convert to seconds by
    ÷ :data:`_ASR_SAMPLE_RATE`. ``speech_pad_ms=0`` keeps the mask tight (padding
    would widen regions and let near-speech hallucinations pass the overlap test);
    ``min_speech_duration_ms=0`` keeps short real interjections IN the mask so
    they are never manufactured into out-of-speech inputs for the drop.
    """
    if not vad_available(onnx_pack_root):
        return None
    try:
        from faster_whisper.vad import (  # noqa: PLC0415  (function-local: keep module importable without onnxruntime)
            VadOptions,
            get_speech_timestamps,
        )

        regions = get_speech_timestamps(
            audio,
            vad_options=VadOptions(speech_pad_ms=0, min_speech_duration_ms=0),
            sampling_rate=_ASR_SAMPLE_RATE,
        )
        return [(r["start"] / _ASR_SAMPLE_RATE, r["end"] / _ASR_SAMPLE_RATE) for r in regions]
    except Exception as exc:  # noqa: BLE001 — a VAD failure degrades to "no mask", never aborts
        logger.warning("ASR: Silero VAD speech-mask failed (%s); non-speech overlap drop disabled.", exc)
        return None


def _speech_overlap(start: float, end: float, speech: list[tuple[float, float]]) -> float:
    """Fraction of the segment span ``[start, end]`` (seconds) covered by *speech*."""
    if end <= start:
        return 0.0
    covered = 0.0
    for a, b in speech:
        lo, hi = max(start, a), min(end, b)
        if hi > lo:
            covered += hi - lo
    return covered / (end - start)


def _is_nonspeech_ct2_segment(seg, speech: list[tuple[float, float]] | None) -> bool:
    """Return True for a CT2 segment to drop as non-speech (vad_filter-OFF path).

    Reuses Whisper's own gates (:func:`_is_junk_segment`: compression_ratio /
    avg_logprob) and adds two non-speech drops (see the module constants):
      * ``no_speech_prob >= _CONFIDENT_NONSPEECH_PROB`` — confident non-speech.
      * out-of-speech (overlap < :data:`_MIN_SPEECH_OVERLAP`) AND corroborated by
        a repetition (compression_ratio) or a long span (:data:`_NONSPEECH_MIN_DURATION_S`).

    The overlap arm never fires on ``no_speech_prob`` alone, so a real line the
    VAD merely missed (short, out-of-speech, any nsp) survives. ``getattr``
    defaults keep this a no-op on field-less fakes. Applied ONLY to CT2 segments;
    the cpp path has no nsp/compression and uses :func:`_is_junk_segment` +
    :func:`_is_low_probability_cpp_segment`.
    """
    if _is_junk_segment(seg):
        return True
    no_speech_prob = getattr(seg, "no_speech_prob", 0.0)
    if no_speech_prob is not None and no_speech_prob >= _CONFIDENT_NONSPEECH_PROB:
        return True
    if speech is not None:
        start = getattr(seg, "start", 0.0)
        end = getattr(seg, "end", 0.0)
        if _speech_overlap(start, end, speech) < _MIN_SPEECH_OVERLAP:
            compression_ratio = getattr(seg, "compression_ratio", 0.0) or 0.0
            if compression_ratio > _MAX_COMPRESSION_RATIO or (end - start) > _NONSPEECH_MIN_DURATION_S:
                return True
    return False


def _is_low_probability_cpp_segment(seg) -> bool:
    """Return True for a whisper.cpp segment to drop on low geom-mean probability.

    Only drops when ``probability`` is a real number strictly below the floor; a
    ``None`` or ``NaN`` probability (extract_probability off, or an undefined
    score) is "unknown" and kept. This is the cpp counterpart to
    :func:`_is_junk_segment`, which is a harmless no-op on cpp segments (they
    lack compression_ratio/avg_logprob).
    """
    prob = getattr(seg, "probability", None)
    if prob is None or not isinstance(prob, (int, float)) or math.isnan(prob):
        return False
    return bool(prob < _MIN_CPP_SEGMENT_PROBABILITY)


def _cpp_ggml_present(model_name: str, models_root: Path) -> bool:
    """Return True iff *model_name*'s ggml acoustic file is on disk under *models_root*.

    Thin seam over ``ggml_model_installer`` so the cascade can short-circuit to
    CT2 CPU when the GPU weights were never downloaded. Never raises — an unknown
    model (no spec) or a stat failure degrades to "absent".
    """
    try:
        return ggml_model_installer.is_ggml_downloaded(model_name, models_root)
    except Exception:  # noqa: BLE001 — unknown model / odd path → treat as absent
        return False


def _cpp_decode_params(models_root: Path) -> dict[str, Any]:
    """Build the whisper.cpp decode params, mirroring the CT2 anti-hallucination intent.

    ``language="ja"`` and ``no_context=True`` (disable conditioning on previously
    decoded text, killing runaway repetition loops — the cpp analogue of CT2's
    ``condition_on_previous_text=False``). entropy_thold / logprob_thold /
    no_speech_thold are left at whisper.cpp defaults: they already suppress
    repetition and low-confidence output the same way CT2's gates do. VAD is
    enabled ONLY when the Silero ggml is present — a missing VAD file never fails
    the build, it just runs without VAD (the flags above still apply).
    """
    params: dict[str, Any] = {"language": "ja", "no_context": True}
    if ggml_model_installer.is_vad_downloaded(models_root):
        params["vad"] = True
        params["vad_model_path"] = str(ggml_model_installer.vad_model_path(models_root))
    else:
        params["vad"] = False
    return params


def _cpp_segments(model, audio, *, duration_s, progress_cb, cancel_event, decode_params):
    """Yield CT2-shaped segments from a whisper.cpp (pywhispercpp) decode.

    pywhispercpp's ``transcribe`` is a single blocking call returning a fully
    materialized ``List[Segment]`` — there is no lazy iterator — so live progress
    MUST come from ``new_segment_callback`` (iterating the returned list is
    instant and would snap the bar 0→100). The callback fires once per segment as
    whisper.cpp emits it; ``abort_callback`` lets a set ``cancel_event`` stop the
    in-flight decode. Each returned ``Segment`` is re-yielded as a
    ``types.SimpleNamespace`` carrying ``start``/``end`` (t0/t1 are CENTISECONDS:
    seconds = value / 100.0), ``text`` and ``probability`` — the SAME duck type
    the CT2 result loop consumes (start/end/text).
    """

    def _on_segment(seg) -> None:
        if progress_cb is not None and duration_s > 0:
            progress_cb(min(seg.t1 / 100.0 / duration_s, 1.0))

    def _should_abort() -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    segments = model.transcribe(
        audio,
        new_segment_callback=_on_segment,
        abort_callback=_should_abort,
        extract_probability=True,
        **decode_params,
    )
    for seg in segments:
        yield types.SimpleNamespace(
            start=seg.t0 / 100.0,
            end=seg.t1 / 100.0,
            text=seg.text,
            probability=seg.probability,
        )


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

    # Pick the engine from *device*. CT2 (cpu/cuda) is the unchanged default; only
    # vulkan/auto with a usable Vulkan device and a present ggml model route to the
    # whisper.cpp engine. A GPU/engine problem there never crashes the run — it
    # falls back to a full CT2 CPU re-decode.
    if _use_whisper_cpp_engine(device, model_name, models_root):
        return _transcribe_cpp(
            audio,
            model_name=model_name,
            models_root=models_root,
            duration_s=duration_s,
            cancel_event=cancel_event,
            progress_cb=progress_cb,
            cuda_libs_root=cuda_libs_root,
            onnx_pack_root=onnx_pack_root,
        )

    # CT2 only understands cpu/cuda/auto; a 'vulkan' request that did not route to
    # whisper.cpp (backend lib absent — e.g. the CPU-only PyPI wheel — no Vulkan
    # device, or missing ggml) falls back to CT2 "auto", NOT forced CPU: the user
    # asked for GPU, so use a CUDA GPU if one is present and usable (auto already
    # rebuilds on CPU for no-GPU / CUDA-init failure). This salvages GPU speed for
    # a persisted device='vulkan' config before the user reopens Settings (where
    # the now-unavailable option is dropped to 'auto' by the load_from_config
    # hygiene); strictly >= the old forced-CPU behaviour.
    ct2_device = "auto" if device == "vulkan" else device
    return _transcribe_ct2(
        audio,
        model_name=model_name,
        models_root=models_root,
        duration_s=duration_s,
        cancel_event=cancel_event,
        progress_cb=progress_cb,
        device=ct2_device,
        cuda_libs_root=cuda_libs_root,
        onnx_pack_root=onnx_pack_root,
    )


def _use_whisper_cpp_engine(device: str, model_name: str, models_root: Path) -> bool:
    """Decide whether the whisper.cpp engine should run, given *device*.

    Implements the device→engine cascade (CT2 behaviour unchanged):
      * ``cpu`` / ``cuda`` → never (pure CT2; the seam is not even queried).
      * ``vulkan`` → cpp iff whisper.cpp is available AND a Vulkan device exists;
        else CT2 CPU.
      * ``auto`` → CT2 CUDA wins if a CUDA GPU is present; otherwise cpp iff
        whisper.cpp is available AND a Vulkan device exists; else CT2 CPU.

    When cpp is otherwise selected but the ggml acoustic file is missing on disk,
    logs and returns False so the caller falls back to CT2 CPU (never crashes).
    """
    if device in ("cpu", "cuda"):
        return False

    if device == "vulkan":
        if not _engine.whisper_cpp_available():
            logger.info("ASR: device='vulkan' but whisper.cpp is unavailable; using CPU.")
            return False
        if _engine.vulkan_device_count() <= 0:
            logger.info("ASR: device='vulkan' but no Vulkan device is available; using CPU.")
            return False
    elif device == "auto":
        # CUDA wins over Vulkan when both are present.
        if _engine.cuda_device_count() > 0:
            return False
        if not (_engine.whisper_cpp_available() and _engine.vulkan_device_count() > 0):
            return False
    else:  # pragma: no cover — config validates device into the known set.
        return False

    if not _cpp_ggml_present(model_name, models_root):
        logger.warning(
            "ASR: whisper.cpp selected but the ggml model for %r is not downloaded; falling back to CPU.",
            model_name,
        )
        return False
    return True


def _transcribe_cpp(
    audio,
    *,
    model_name: str,
    models_root: Path,
    duration_s: float,
    cancel_event,
    progress_cb: Callable[[float], None] | None,
    cuda_libs_root: Path | None,
    onnx_pack_root: Path | None,
) -> list[tuple[float, float, str]]:
    """Transcribe via the whisper.cpp (pywhispercpp) engine, with a CT2 CPU fallback.

    Wraps the ENTIRE attempt (Model construction + the single blocking
    ``transcribe`` call) in try/except: pywhispercpp returns a fully-materialized
    list AFTER decoding, so the CT2 peek-first-segment deferred-failure trick does
    not apply — on ANY exception we log and re-run on CT2 CPU (a full CPU
    re-decode), so a GPU/driver failure never crashes the run. Junk filtering,
    cancel checks and live progress mirror the CT2 loop.
    """
    decode_params = _cpp_decode_params(models_root)
    try:
        model_cls = _engine.get_whisper_cpp_model_cls()
        model = model_cls(str(ggml_model_installer.ggml_model_path(model_name, models_root)))

        results: list[tuple[float, float, str]] = []
        for seg in _cpp_segments(
            model,
            audio,
            duration_s=duration_s,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            decode_params=decode_params,
        ):
            if cancel_event is not None and cancel_event.is_set():
                break
            # _is_junk_segment is a harmless no-op on cpp segments (no
            # compression_ratio/avg_logprob); the probability floor is the cpp
            # path's actual junk drop.
            if not _is_junk_segment(seg) and not _is_low_probability_cpp_segment(seg):
                results.append((seg.start, seg.end, seg.text.strip()))

        if progress_cb is not None:
            progress_cb(1.0)
        return results
    except Exception as exc:  # noqa: BLE001 — any cpp/GPU failure → full CT2 CPU re-decode
        logger.warning("ASR: whisper.cpp transcription failed (%s); falling back to CPU.", exc)
        return _transcribe_ct2(
            audio,
            model_name=model_name,
            models_root=models_root,
            duration_s=duration_s,
            cancel_event=cancel_event,
            progress_cb=progress_cb,
            device="cpu",
            cuda_libs_root=cuda_libs_root,
            onnx_pack_root=onnx_pack_root,
        )


def _transcribe_ct2(
    audio,
    *,
    model_name: str,
    models_root: Path,
    duration_s: float,
    cancel_event,
    progress_cb: Callable[[float], None] | None,
    device: str,
    cuda_libs_root: Path | None,
    onnx_pack_root: Path | None,
) -> list[tuple[float, float, str]]:
    """The faster-whisper (ctranslate2) transcription path — unchanged behaviour.

    Honours ``device`` in ``{"cpu", "cuda", "auto"}`` with the established CPU
    fallback (construction failure and deferred CUDA-runtime failure both rebuild
    on CPU). Extracted verbatim from the original ``transcribe`` body so the
    cpu/cuda paths stay byte-for-byte behaviourally identical.
    """
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
    #  * word_timestamps=True — REQUIRED: it unlocks hallucination_silence_threshold
    #    (faster-whisper only applies that gate when word timestamps are on) and
    #    snaps each segment's start/end onto real word boundaries. Do NOT remove it
    #    as "unused": nothing else references it, but dropping it silently disables
    #    hallucination_silence_threshold and loosens every subtitle boundary.
    #  * hallucination_silence_threshold — skips long silent gaps; works even
    #    without VAD (the bundle path).
    #  * temperature=0.0 — collapse Whisper's decode-temperature fallback ladder to
    #    greedy, so the same media transcribes to the SAME text run-to-run (the app
    #    re-mines media and de-dups on episode identity; a nondeterministic
    #    transcript would defeat that). With fallback off, a high compression_ratio
    #    no longer means "already retried and failed", so the compression drop is
    #    gated on being out-of-speech (see _is_nonspeech_ct2_segment) — a looped but
    #    real line inside a speech region is never deleted by compression alone.
    #  * vad_filter=False — DELIBERATELY OFF. With it ON, faster-whisper removes
    #    silence, transcribes the concatenated speech, then restores the timeline;
    #    Whisper groups words across removed silences into one segment whose restored
    #    start/end SPAN the gap (the multi-minute-subtitle bug) and mis-align single
    #    moras (the single-character-fragment bug). Transcribing the real timeline
    #    instead yields tight, coherent segments; non-speech hallucinations are then
    #    removed by _is_nonspeech_ct2_segment against an independent Silero mask.
    transcribe_kwargs = {
        "language": "ja",
        "condition_on_previous_text": False,
        "word_timestamps": True,
        "hallucination_silence_threshold": 2.0,
        "temperature": 0.0,
        "vad_filter": False,
    }
    # Independent Silero speech mask for the non-speech drop (None when the VAD
    # cannot run — then only the confidence gates apply). Computed once.
    speech = _speech_mask(audio, onnx_pack_root)
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
        if not _is_nonspeech_ct2_segment(seg, speech):
            results.append((seg.start, seg.end, seg.text.strip()))
        if progress_cb is not None and duration_s > 0:
            progress_cb(min(seg.end / duration_s, 1.0))

    if progress_cb is not None:
        progress_cb(1.0)

    return results

"""Import seam for faster-whisper.

This module is the ONLY place in the codebase that touches faster_whisper
names. All other ASR code imports through these three functions so that:

1. Default CI (no ``[asr]`` extra) stays green — no ImportError at module load.
2. Unit tests can monkeypatch ``available``, ``get_whisper_model_cls``, and
   ``get_download_fn`` without importing the real library.

Never add ``import faster_whisper`` at module top level.
"""

import importlib.util


def available() -> bool:
    """Return True iff faster-whisper AND its native backend are importable.

    Uses ``importlib.util.find_spec`` so no actual import occurs (and no
    initialisation side-effects). Both ``faster_whisper`` and ``ctranslate2``
    must be findable; missing either returns False.
    """
    return (
        importlib.util.find_spec("faster_whisper") is not None and importlib.util.find_spec("ctranslate2") is not None
    )


def get_whisper_model_cls():
    """Return ``faster_whisper.WhisperModel`` (function-local import).

    Raises:
        ImportError: If faster_whisper is not installed.
    """
    import faster_whisper  # noqa: PLC0415  (intentional function-local import)

    return faster_whisper.WhisperModel


def get_download_fn():
    """Return ``faster_whisper.download_model`` (function-local import).

    Raises:
        ImportError: If faster_whisper is not installed.
    """
    import faster_whisper  # noqa: PLC0415  (intentional function-local import)

    return faster_whisper.download_model


def cuda_device_count() -> int:
    """Return the number of usable CUDA devices, or 0 on ANY failure.

    Function-local ``ctranslate2`` import (the same no-top-level-import rule as
    the rest of this seam) so default CI without the ``[asr]`` extra stays green.
    Degrades to 0 on anything — ImportError (extra not installed), OSError (a
    broken native CUDA runtime), or any other surprise — so callers can treat a
    nonzero return as "a GPU is present and usable" without their own guard.
    """
    try:
        import ctranslate2  # noqa: PLC0415  (intentional function-local import)

        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001 — any failure means "no usable GPU"
        return 0

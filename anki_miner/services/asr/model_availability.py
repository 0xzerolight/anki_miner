"""Is a usable ASR model installed right now?

One predicate, shared by every pre-run gate: the Generate tab's guard and the
YouTube queue's transcription preflight must answer this question identically,
or a run refused in one place succeeds in the other.

Mirrors the runtime engine cascade (``transcriber._use_whisper_cpp_engine``): a
CT2 layout satisfies every device route, while the ggml pair (acoustic + VAD)
satisfies only devices that can route to whisper.cpp (``vulkan``/``auto``) and
only when the backend itself is present — ``cpu``/``cuda`` are pure CT2, so a
CT2-only check would block a fully usable configuration.

Deliberately does NOT probe Vulkan *devices*: ``_engine.vulkan_device_count()``
can re-exec the bundle with a 15 s timeout on first call and this runs on the
GUI thread. If the device turns out to be absent at run time the transcriber
falls back to CT2 and surfaces the real error.

Lives here rather than in ``model_manager`` because the import graph runs
``ggml_model_installer -> model_manager -> _engine``; a ``model_manager`` that
imported ``ggml_model_installer`` would cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anki_miner.services.asr import _engine, ggml_model_installer, model_manager

if TYPE_CHECKING:
    from anki_miner.config.config import AnkiMinerConfig

__all__ = ["usable_model_installed"]


def usable_model_installed(config: AnkiMinerConfig) -> bool:
    """Whether transcription can start without downloading a model first.

    Any probe surprise counts as not-installed: the run would fail anyway, and
    a gate must never raise.
    """
    if model_manager.is_downloaded(config.asr_model, config.asr_models_root):
        return True
    if config.asr_device not in ("vulkan", "auto"):
        return False
    try:
        return (
            _engine.whisper_cpp_available()
            and ggml_model_installer.is_ggml_downloaded(config.asr_model, config.asr_models_root)
            and ggml_model_installer.is_vad_downloaded(config.asr_models_root)
        )
    except Exception:  # noqa: BLE001 - a probe failure means "not usable", never a crash
        return False

"""What the speech and OCR commands need on this machine (API.md, "check").

Each probe returns the English message ``check`` shows and a command refuses
with (``SETUP_ERROR``), or None when ready. File probes only: no model is
loaded and no engine is started.
"""

from __future__ import annotations

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import config_language, get_profile

NO_SPEECH_MODEL = (
    "The speech model {model} is not installed. Install it with setup --speech-model, "
    "or in Anki Miner: Settings -> Transcription & Alignment."
)
_NO_SPEECH_ENGINE = (
    "The speech engine is not installed. Install it with setup --speech-model, "
    "or in Anki Miner: Settings -> Transcription & Alignment."
)
_NO_MANGA_OCR = "Manga OCR is not available for this language."
_NO_MOKURO = "mokuro is not installed. Install it with setup --ocr, or in Anki Miner: Utilities -> Manga OCR."
_NO_MANGA_PARTS = (
    "Manga OCR's text recognizer for this language is not installed. "
    "Install it with setup --ocr, or in Anki Miner: Utilities -> Manga OCR."
)
_NO_VIDEO_OCR = "Video OCR is not available for this language."
_NO_VIDEO_OCR_PARTS = (
    "Video OCR's engine or models are not installed. "
    "Install them with setup --ocr, or in Anki Miner: Utilities -> Video OCR."
)


def speech_problem(config: AnkiMinerConfig) -> str | None:
    """A usable model and an engine that runs it.

    ``usable_model_installed`` takes a downloaded CT2 model as enough; a build
    without the CT2 engine (the bundle before its engine pack is installed)
    still cannot run it unless the whisper.cpp route can.
    """
    from anki_miner.services.asr import _engine, model_manager
    from anki_miner.services.asr.model_availability import whisper_cpp_route_ready

    if whisper_cpp_route_ready(config):
        return None
    if not model_manager.is_downloaded(config.asr_model, config.asr_models_root):
        return NO_SPEECH_MODEL.format(model=config.asr_model)
    return None if _engine.available() else _NO_SPEECH_ENGINE


def manga_ocr_problem(config: AnkiMinerConfig) -> str | None:
    """mokuro, and the language's recognizer where it uses one (Utilities -> Manga OCR's own probe)."""
    from anki_miner.services.mokuro_installer import manga_ocr_state

    profile = get_profile(config_language(config))
    if "manga_ocr" not in profile.capabilities:
        return _NO_MANGA_OCR
    state = manga_ocr_state(config)
    if state:
        return None
    return _NO_MANGA_PARTS if profile.ocr_recognizer and state.mokuro else _NO_MOKURO


def video_ocr_problem(config: AnkiMinerConfig) -> str | None:
    """onnxruntime and the language's models (Utilities -> Video OCR's own probe)."""
    from anki_miner.services.ocr import model_installer
    from anki_miner.services.video_ocr import runtime

    profile = get_profile(config_language(config))
    if "video_ocr" not in profile.capabilities:
        return _NO_VIDEO_OCR
    ready = runtime.runtime_ready(config.onnx_pack_root) and model_installer.is_installed(
        config.video_ocr_models_root, runtime.required_models(profile)
    )
    return None if ready else _NO_VIDEO_OCR_PARTS

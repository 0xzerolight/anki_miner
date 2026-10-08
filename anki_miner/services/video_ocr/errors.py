"""Video OCR errors, kept numpy-free so GUI modules can import them at startup."""

from __future__ import annotations

from anki_miner.exceptions import SetupError


class EngineLoadError(SetupError):
    """onnxruntime or a model file could not be loaded."""


class FrameSourceError(SetupError):
    """ffmpeg could not decode the video (the message carries its stderr tail)."""

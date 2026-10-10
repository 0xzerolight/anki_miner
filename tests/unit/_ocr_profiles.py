"""Give a language a PP-OCR recogniser in one module's view (no profile has one before the grants)."""

from __future__ import annotations

from dataclasses import replace


def with_recognizer(monkeypatch, module, code: str, key: str) -> None:
    """Make ``module.get_profile(code)`` return that profile with ``ocr_recognizer=key``."""
    real = module.get_profile
    monkeypatch.setattr(module, "get_profile", lambda c: replace(real(c), ocr_recognizer=key) if c == code else real(c))

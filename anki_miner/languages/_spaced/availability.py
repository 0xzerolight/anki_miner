"""Can this machine mine a spaCy language? (the profile's ``unavailable_reason``).

Two hard requirements: the spaCy runtime and the language's model package.
Each is satisfied by an importable package (a pip install with the language's
extra, plus the model wheel) OR by the in-app pack on disk (``_spacy`` for the
runtime, the language's own pack for the model). ``find_spec`` answers without
importing anything, so probing costs nothing. No language branch: the code,
English name and model package are the caller's data.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from importlib.util import find_spec

from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

SPACY_RUNTIME_PACK = "_spacy"
SPACY_IMPORT_NAME = "spacy"


def _importable(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError) as exc:  # installed and broken: say so in the log
        log_summary(
            logger,
            "Language module probe failed",
            level=logging.WARNING,
            module=name,
            exc=f"{type(exc).__name__}: {exc}",
        )
        return False


def _pack_component_present(code: str, import_name: str) -> bool:
    from anki_miner.services.language_pack_installer import component_path

    return component_path(code, import_name) is not None


def spaced_missing_reason(code: str, english_name: str, model_package: str) -> Callable[[], str | None]:
    """Build the probe; the returned callable runs at call time, never at profile build."""

    def reason() -> str | None:
        runtime = _importable(SPACY_IMPORT_NAME) or _pack_component_present(SPACY_RUNTIME_PACK, SPACY_IMPORT_NAME)
        model = _importable(model_package) or _pack_component_present(code, model_package)
        if runtime and model:
            return None
        if getattr(sys, "frozen", False):
            return (
                f"{english_name} mining needs the {english_name} language pack. "
                "Download it in Settings -> Mining Language."
            )
        if not runtime:
            return (
                f'{english_name} mining needs spaCy. Install with: pip install "anki-miner[{code}]" - '
                f"or download the {english_name} pack in Settings -> Mining Language."
            )
        return f"{english_name} mining needs {model_package}. Download the {english_name} pack in Settings -> Mining Language."

    return reason

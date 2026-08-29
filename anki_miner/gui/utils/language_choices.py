"""Which mining languages this build can actually offer.

``AVAILABLE_LANGUAGES`` is the declared vocabulary and lives in the package
``__init__`` (Stage 0); ``get_profile`` lives in ``languages.registry``, which
is the only import surface consumers use - ``languages/__init__.py`` stays
sealed because ``profile.py`` imports ``services.resource_catalog`` at module
level. A code is only offerable once ``get_profile`` builds, and a tokenizer
extra can be absent from any build, so the selector resolves every code and
drops what does not resolve rather than assuming.
"""

from __future__ import annotations

import logging

from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile

logger = logging.getLogger(__name__)


def available_mining_languages() -> tuple[tuple[str, str], ...]:
    """``(code, native display name)`` for every language whose profile builds."""
    choices: list[tuple[str, str]] = []
    for code in AVAILABLE_LANGUAGES:
        try:
            profile = get_profile(code)
        except (LookupError, ValueError, ImportError) as exc:
            # LookupError covers the registry miss (KeyError); ImportError covers
            # a build without that language's tokenizer extra.
            logger.info("Mining language %r is declared but not available here: %s", code, exc)
            continue
        # Building the profile proves nothing about the packages it needs at
        # parse time: every zh third-party import is function-local, so the
        # profile builds on a machine with none of them installed. The profile's
        # own probe is what decides, and a language that cannot mine a word is
        # not offered as a destination.
        probe = profile.unavailable_reason
        reason = probe() if probe is not None else None
        if reason:
            logger.info("Mining language %r is not available here: %s", code, reason)
            continue
        choices.append((profile.code, profile.display_name))
    return tuple(choices)

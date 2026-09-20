"""Runtime probe for the yue dependency set (spec 11).

The profile is always constructible -- the GUI needs the language in its
selector and the setup notice has to name what is missing. ``find_spec`` answers
without executing pycantonese, so probing costs nothing.

Both packages are hard requirements: ``rustling`` carries the compiled
segmenter and tagger pycantonese calls, and the pack installs the two together.
"""

from __future__ import annotations

import logging
import sys
from importlib.util import find_spec

from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

YUE_REQUIRED_PACKAGES: tuple[str, ...] = ("pycantonese", "rustling")

#: A frozen bundle has no pip, so naming a package is dead advice -- this names
#: the download button directly instead.
YUE_FROZEN_PACK_REASON = (
    "Cantonese mining needs the Cantonese language pack. Download it in Settings -> Mining Language."
)

#: The sentence naming the in-app download for a pip build.
YUE_PACK_DOWNLOAD_HINT = "or download the Cantonese pack in Settings -> Mining Language."


def _installed(name: str) -> bool:
    """Return True when *name* is importable, reporting a BROKEN install.

    A clean ``None`` is an absence and says so quietly. A probe that RAISES is
    the opposite diagnosis - the package is on disk and unimportable (a missing
    shared library, a half-extracted pack) - and reaches the user through the
    same "needs pycantonese" sentence, so the log is the only place the two
    differ.
    """
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError) as exc:
        log_summary(
            logger,
            "Language module probe failed",
            level=logging.WARNING,
            module=name,
            exc=f"{type(exc).__name__}: {exc}",
        )
        return False


def yue_missing_required_reason() -> str | None:
    """Why Cantonese mining cannot run, or None when it can."""
    missing = [name for name in YUE_REQUIRED_PACKAGES if not _installed(name)]
    if not missing:
        return None
    if getattr(sys, "frozen", False):
        # No pip in a bundle: name the download button instead of a package the
        # user cannot install.
        return YUE_FROZEN_PACK_REASON
    line = f"Cantonese mining needs {', '.join(missing)}. Install with: pip install \"anki-miner[yue]\""
    return f"{line} - {YUE_PACK_DOWNLOAD_HINT}"

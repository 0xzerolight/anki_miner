"""Runtime probe for the th dependency (spec 11).

The profile is always constructible -- the GUI needs the language in its
selector and the setup notice has to name what is missing. ``find_spec`` answers
without executing pythainlp, so probing costs nothing.
"""

from __future__ import annotations

import logging
import sys
from importlib.util import find_spec

from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

TH_REQUIRED_PACKAGES: tuple[str, ...] = ("pythainlp",)

#: A frozen bundle has no pip, so naming a package is dead advice -- this names
#: the download button directly instead.
TH_FROZEN_PACK_REASON = "Thai mining needs the Thai language pack. Download it in Settings -> Mining Language."

#: The sentence naming the in-app download for a pip build.
TH_PACK_DOWNLOAD_HINT = "or download the Thai pack in Settings -> Mining Language."


def _installed(name: str) -> bool:
    """Return True when *name* is importable, reporting a BROKEN install.

    A clean ``None`` is an absence and says so quietly. A probe that RAISES is
    the opposite diagnosis - the package is on disk and unimportable (a missing
    shared library, a half-extracted pack) - and reaches the user through the
    same "needs pythainlp" sentence, so the log is the only place the two differ.
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


def th_missing_required_reason() -> str | None:
    """Why Thai mining cannot run, or None when it can."""
    missing = [name for name in TH_REQUIRED_PACKAGES if not _installed(name)]
    if not missing:
        return None
    if getattr(sys, "frozen", False):
        # No pip in a bundle: name the download button instead of a package
        # the user cannot install.
        return TH_FROZEN_PACK_REASON
    line = f"Thai mining needs {', '.join(missing)}. Install with: pip install \"anki-miner[th]\""
    return f"{line} - {TH_PACK_DOWNLOAD_HINT}"

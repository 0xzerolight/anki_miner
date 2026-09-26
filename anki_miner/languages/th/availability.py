"""Runtime probe for the th dependency (spec 11).

The profile is always constructible -- the GUI needs the language in its
selector and the setup notice has to name what is missing. ``find_spec`` answers
without executing pythainlp, so probing costs nothing.
"""

from __future__ import annotations

import sys

from anki_miner.languages._spaced.availability import module_importable

TH_REQUIRED_PACKAGES: tuple[str, ...] = ("pythainlp",)

#: A frozen bundle has no pip, so naming a package is dead advice -- this names
#: the download button directly instead.
TH_FROZEN_PACK_REASON = "Thai mining needs the Thai language pack. Download it in Settings -> Mining Language."

#: The sentence naming the in-app download for a pip build.
TH_PACK_DOWNLOAD_HINT = "or download the Thai pack in Settings -> Mining Language."


def th_missing_required_reason() -> str | None:
    """Why Thai mining cannot run, or None when it can."""
    missing = [name for name in TH_REQUIRED_PACKAGES if not module_importable(name)]
    if not missing:
        return None
    if getattr(sys, "frozen", False):
        # No pip in a bundle: name the download button instead of a package
        # the user cannot install.
        return TH_FROZEN_PACK_REASON
    line = f"Thai mining needs {', '.join(missing)}. Install with: pip install \"anki-miner[th]\""
    return f"{line} - {TH_PACK_DOWNLOAD_HINT}"

"""Runtime probe for the optional zh dependency set (spec 11).

The profile is always constructible — the GUI needs the language in its
selector, and the setup notice has to name what is missing. Nothing here
imports the packages; ``find_spec`` answers without executing them, so probing
costs nothing on a machine that has none of them.
"""

from __future__ import annotations

import sys
from importlib.util import find_spec

# Hard requirements: without a tokenizer or readings there is no zh mining.
ZH_REQUIRED_PACKAGES: tuple[str, ...] = ("jieba", "pypinyin")
# Optional: OpenCC only adds simplified/traditional lookup fallbacks, so its
# absence degrades a feature instead of disabling the language. It is still
# reported, because a user whose traditional dictionary stops matching a
# simplified subtitle needs to be told why.
ZH_OPTIONAL_PACKAGES: tuple[str, ...] = ("opencc",)

#: A frozen bundle has no pip, so naming a package is dead advice — this names
#: the download button directly instead.
ZH_FROZEN_PACK_REASON = "Chinese mining needs the Chinese language pack. Download it in Settings -> Mining Language."

#: The sentence naming the in-app download for a pip build. Required tier only:
#: OpenCC pins one ABI, so the pack cannot satisfy the optional tier and the
#: hint would send a user to a button that will not fix their install.
ZH_PACK_DOWNLOAD_HINT = "or download the Chinese pack in Settings -> Mining Language."


def _installed(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _reason(missing: list[str], *, pack_hint: bool) -> str | None:
    if not missing:
        return None
    if getattr(sys, "frozen", False):
        # No pip in a bundle: name the download button instead of a package
        # the user cannot install.
        return ZH_FROZEN_PACK_REASON
    line = f"Chinese mining needs {', '.join(missing)}. Install with: pip install \"anki-miner[zh]\""
    return f"{line} - {ZH_PACK_DOWNLOAD_HINT}" if pack_hint else line


def zh_unavailable_reason() -> str | None:
    """Names every missing zh package, or ``None`` when the stack is complete."""
    missing = [name for name in ZH_REQUIRED_PACKAGES + ZH_OPTIONAL_PACKAGES if not _installed(name)]
    return _reason(missing, pack_hint=False)


def zh_missing_required_reason() -> str | None:
    """Names the missing HARD requirements only - the availability gate.

    This, not :func:`zh_unavailable_reason`, is what the profile hands the GUI:
    a build missing only OpenCC still mines Chinese (the variant lookups come
    back empty), so gating on the full set would take the language out of the
    selector and refuse the switch over a degraded feature.
    """
    return _reason([name for name in ZH_REQUIRED_PACKAGES if not _installed(name)], pack_hint=True)

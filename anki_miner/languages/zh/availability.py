"""Runtime probe for the optional zh dependency set (spec 11).

The profile is always constructible — the GUI needs the language in its
selector, and the setup notice has to name what is missing. Nothing here
imports the packages; ``find_spec`` answers without executing them, so probing
costs nothing on a machine that has none of them.
"""

from __future__ import annotations

from importlib.util import find_spec

# Hard requirements: without a tokenizer or readings there is no zh mining.
ZH_REQUIRED_PACKAGES: tuple[str, ...] = ("jieba", "pypinyin")
# Optional: OpenCC only adds simplified/traditional lookup fallbacks, so its
# absence degrades a feature instead of disabling the language. It is still
# reported, because a user whose traditional dictionary stops matching a
# simplified subtitle needs to be told why.
ZH_OPTIONAL_PACKAGES: tuple[str, ...] = ("opencc",)


def _installed(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def zh_unavailable_reason() -> str | None:
    """Names every missing zh package, or ``None`` when the stack is complete."""
    missing = [name for name in ZH_REQUIRED_PACKAGES + ZH_OPTIONAL_PACKAGES if not _installed(name)]
    if not missing:
        return None
    return f"Chinese mining needs {', '.join(missing)}. Install with: pip install \"anki-miner[zh]\""

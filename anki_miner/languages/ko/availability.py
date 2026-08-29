"""Runtime probe for the optional ko dependency set (spec 11).

The profile is always constructible — every kiwipiepy import in this package is
function-local, because the GUI needs the language in its selector and the setup
notice has to name what is missing. Nothing here imports the packages;
``find_spec`` answers without executing them, so probing costs nothing on a
machine that has neither.

Both packages are HARD requirements, so there is no optional tier and no
``ko_unavailable_reason`` counterpart to the zh module's: ``Kiwi()`` raises
without the model, which leaves nothing degraded to fall back to.
"""

from __future__ import annotations

from importlib.util import find_spec

#: Import names, not pip names — ``find_spec`` takes the module. The install
#: line in the message is what the user acts on, and the extra pulls both.
KO_REQUIRED_PACKAGES: tuple[str, ...] = ("kiwipiepy", "kiwipiepy_model")


def _installed(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def ko_missing_required_reason() -> str | None:
    """Names the missing hard requirements — the availability gate.

    This is what the profile hands the GUI. Without it the selector offers
    Korean and the switch proceeds on an install with no engine, and the failure
    surfaces as "No tokenizer registered" mid-mining, long after the choice.
    """
    missing = [name for name in KO_REQUIRED_PACKAGES if not _installed(name)]
    if not missing:
        return None
    return f"Korean mining needs {', '.join(missing)}. Install with: pip install \"anki-miner[ko]\""

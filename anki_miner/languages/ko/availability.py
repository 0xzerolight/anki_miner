"""Runtime probe for the optional ko dependency set (spec 11).

The profile is always constructible — every kiwipiepy import in this package is
function-local, because the GUI needs the language in its selector and the setup
notice has to name what is missing. Nothing here imports the packages;
``find_spec`` answers without executing them, so probing costs nothing on a
machine that has neither.

Both the engine and the model are HARD requirements, so there is no optional tier
and no ``ko_unavailable_reason`` counterpart to the zh module's: ``Kiwi()`` raises
without the model, which leaves nothing degraded to fall back to. They are not
satisfied the same way, though. The engine comes from the ``[ko]`` extra, the
bundle, or the download pack — a pack install lands it on ``sys.path``, so
``find_spec`` sees it like any other. The MODEL is ~88 MB and stays out of the
bundle, so it is satisfied by the ``kiwipiepy_model`` package OR by the same
pack (``services.language_pack_installer``) — and when neither is there, the
reason names the button rather than a pip line the bundled user cannot run.
"""

from __future__ import annotations

import sys
from importlib.util import find_spec

#: Import names, not pip names — ``find_spec`` takes the module. The install
#: line in the message is what the user acts on, and the extra pulls both.
KO_REQUIRED_PACKAGES: tuple[str, ...] = ("kiwipiepy", "kiwipiepy_model")

#: The one sentence naming the in-app model download for a pip install, where
#: the missing package name is still meaningful. Shared with
#: ``languages.ko.tokenizer`` so a pip-build refusal says the same thing about
#: the same button wherever it surfaces.
KO_MODEL_DOWNLOAD_HINT = "Download the Korean model in Settings -> Mining Language."

#: A frozen bundle has no pip, so naming a package is dead advice — this names
#: the download button directly instead. Shared with ``languages.ko.tokenizer``
#: so a frozen refusal is worded identically everywhere it surfaces.
KO_FROZEN_MODEL_REASON = "Korean mining needs the Korean language pack. Download it in Settings -> Mining Language."


def _installed(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _pack_installed() -> bool:
    """Return True when the in-app model pack is present in the app home."""
    from anki_miner.services.language_pack_installer import component_path

    return component_path("ko", "kiwipiepy_model") is not None


def _available(name: str) -> bool:
    """Return True when requirement *name* is satisfied, however it was met."""
    if name == "kiwipiepy_model":
        return _installed(name) or _pack_installed()
    return _installed(name)


def ko_missing_required_reason() -> str | None:
    """Names the missing hard requirements — the availability gate.

    This is what the profile hands the GUI. Without it the selector offers
    Korean and the switch proceeds on an install with no engine, and the failure
    surfaces as "No tokenizer registered" mid-mining, long after the choice.
    """
    missing = [name for name in KO_REQUIRED_PACKAGES if not _available(name)]
    if not missing:
        return None
    if getattr(sys, "frozen", False):
        # No pip in a bundle: every tier collapses onto the one sentence that
        # names the download button instead of a package the user cannot pip
        # install.
        return KO_FROZEN_MODEL_REASON
    if missing == ["kiwipiepy_model"]:
        # The engine is there, so the model is one button away: a pip line here
        # would be dead advice, and the engine itself is not what is missing.
        return f"Korean mining needs kiwipiepy_model. {KO_MODEL_DOWNLOAD_HINT}"
    # Both tiers, so both routes: the pack ships the engine as well as the
    # model, and a source user who skipped the extra never learns the button
    # exists if the line names only pip.
    return (
        f"Korean mining needs {', '.join(missing)}. "
        'Install with: pip install "anki-miner[ko]" - or download the Korean pack '
        "in Settings -> Mining Language."
    )

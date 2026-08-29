"""Register a throwaway profile for a language whose real one is not built yet.

Only ``ja`` has a registered profile at this point in the transition, but the
importers already accept any code as a stamp — and, since the dictionary
importer folds its key columns with the stamped language's profile, an import
under an unbuilt code now needs *some* profile to resolve. Tests that exercise
a non-ja stamp register one here; the registration is undone by ``monkeypatch``
teardown, so ``available_languages()`` is unchanged for everything else.

Delete the call sites as each real profile lands (zh: Stage 2A, ko: Stage 3).
Its inverse, ``unregister_profile``, arrived with the ko registration: with every
whitelisted code registered, hiding one is the only way to reach the degrade path.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from anki_miner.languages import registry
from anki_miner.languages.profile import LanguageProfile


def register_stub_profile(monkeypatch: Any, code: str, **overrides: Any) -> LanguageProfile:
    """Register a ja-shaped profile under ``code`` for the test's duration."""
    profile = dataclasses.replace(registry.get_profile("ja"), code=code, **overrides)
    monkeypatch.setitem(registry._BUILDERS, code, lambda: profile)
    monkeypatch.setitem(registry._CACHE, code, profile)
    return profile


def unregister_profile(monkeypatch: Any, code: str) -> None:
    """Take ``code``'s profile back out of the registry for the test's duration.

    The inverse of the helper above, and the only way left to reach
    ``registry.config_language``'s degrade branch: every code
    ``config.config._LANGUAGE_CODES`` whitelists now has a registered profile
    (ja Stage 1A, zh Stage 2A, ko Stage 3), and ``__post_init__`` folds anything
    else to ``ja`` before a reader sees it. The branch is still live code guarding
    ~90 read sites, so the tests that cover it hide a real code instead of naming
    one that no longer exists. ``monkeypatch`` restores both dicts on teardown.
    """
    monkeypatch.delitem(registry._BUILDERS, code, raising=False)
    monkeypatch.delitem(registry._CACHE, code, raising=False)

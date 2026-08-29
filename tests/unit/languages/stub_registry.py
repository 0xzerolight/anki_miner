"""Register a throwaway profile for a language whose real one is not built yet.

Only ``ja`` has a registered profile at this point in the transition, but the
importers already accept any code as a stamp — and, since the dictionary
importer folds its key columns with the stamped language's profile, an import
under an unbuilt code now needs *some* profile to resolve. Tests that exercise
a non-ja stamp register one here; the registration is undone by ``monkeypatch``
teardown, so ``available_languages()`` is unchanged for everything else.

Delete the call sites as each real profile lands (zh: Stage 2A, ko: Stage 3).
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

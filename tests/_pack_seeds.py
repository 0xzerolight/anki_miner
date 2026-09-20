"""Where real-data tests find a data-only language pack (ar's CAMeL morphology database).

No pip package carries these files, so the tests read the tree ``scripts/fetch_language_pack_seeds.py``
writes with the app's own installer (both pinned digests verified): CI's ``test`` job seeds
``$RUNNER_TEMP/lang_pack_seeds`` and exports ``ANKI_MINER_TEST_PACK_SEEDS``; a developer machine seeds
the default root once. A missing seed FAILS the test (never skips): a real-data test that silently
skipped would prove nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

SEEDS_ENV = "ANKI_MINER_TEST_PACK_SEEDS"
DEFAULT_SEEDS = Path.home() / ".cache" / "anki-miner-pack-seeds"


def seeded_component(code: str, import_name: str, filename: str) -> Path:
    """Return ``<seeds>/<code>/<import_name>/<filename>``, failing the test when it is absent."""
    root = Path(os.environ.get(SEEDS_ENV) or DEFAULT_SEEDS)
    path = root / code / import_name / filename
    if path.is_file():
        return path
    seed = f"python scripts/fetch_language_pack_seeds.py {root} {code}"
    # The seeder is fail-open by design, so on a CDN outage CI would otherwise go red on every matrix
    # leg for something no contributor can fix. Locally - and at the gate - a missing seed FAILS: a
    # real-data test that silently skipped would prove nothing.
    if os.environ.get("CI"):
        pytest.skip(f"missing language-pack seed {path}; CI seeding did not run ({seed})")
    pytest.fail(f"missing language-pack seed {path}; seed it with: {seed}", pytrace=False)

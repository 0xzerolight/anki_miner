"""Pytest bootstrap for the E2E harness package.

Inserts the worktree root onto ``sys.path`` so ``tests.e2e.<module>`` resolves
the same way under pytest as it does for the standalone runner. ``tests/e2e``
is a regular package (it has an ``__init__.py``); ``tests`` already has one too,
so this only guards against the root not being importable in some invocation
modes.
"""

import pathlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))


@pytest.fixture
def isolated_home(tmp_path: Path):
    """Point the process at a tmp home and restore the patches on teardown.

    Shared across all E2E test modules that need a safe, isolated test home.
    The autouse conftest isolation already redirects the real home, but soak
    tests are explicit about which home they use — this pins a per-test tmp home
    and restores in-process binding patches on teardown.
    """
    from tests._home_isolation import restore_home_patches, set_test_home

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    saved = set_test_home(home)
    try:
        yield home
    finally:
        restore_home_patches(saved)

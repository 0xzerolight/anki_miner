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


def pytest_collection_modifyitems(items):
    """Exempt e2e AND network-marked harness tests from the global per-test
    timeout (pyproject sets 120s as a deadlock backstop for unit tests).
    Real-service e2e/soak runs drive ffmpeg, Anki, and multi-session loops that
    legitimately exceed it. ``network``-marked tests here are the fake-AnkiConnect
    process runs — the multi-session faithful soaks do several real ffmpeg
    extractions per test and can blow the 120s cap on slow machines."""
    for item in items:
        if item.get_closest_marker("e2e") or item.get_closest_marker("network"):
            item.add_marker(pytest.mark.timeout(0))


@pytest.fixture
def fake_anki():
    """A started FakeAnkiConnect server, pre-seeded with the E2E note model.

    The pre-seed matters for gateway-less driver tests: a process run hits the
    app's preflight (``verify_card_target``: ``modelNames`` +
    ``modelFieldNames``), which raises ``SetupError`` unless the harness note
    type already exists. Soak paths would create it via the gateway's
    ``ensure_test_model()`` anyway; the seed makes both paths uniform.

    Tests using this fixture talk real loopback HTTP — mark them
    ``@pytest.mark.network`` or the socket tripwire fails them.
    """
    from tests.e2e.config import E2EConfig
    from tests.e2e.fake_ankiconnect import FakeAnkiConnect

    server = FakeAnkiConnect().start()
    server.seed_model(E2EConfig().note_type, ["Front", "Back"])
    try:
        yield server
    finally:
        server.stop()


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

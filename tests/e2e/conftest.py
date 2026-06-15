"""Pytest bootstrap for the E2E harness package.

Inserts the worktree root onto ``sys.path`` so ``tests.e2e.<module>`` resolves
the same way under pytest as it does for the standalone runner. ``tests/e2e``
is a regular package (it has an ``__init__.py``); ``tests`` already has one too,
so this only guards against the root not being importable in some invocation
modes.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

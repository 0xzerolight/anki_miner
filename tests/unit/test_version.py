"""Regression tests for __version__ source-of-truth.

Issue #10: ``__version__`` used to be computed at import time via
``importlib.metadata.version("anki-miner")``, which reads
``*.dist-info/METADATA`` from the filesystem. PyInstaller-frozen Windows
installs that overlay onto an older version left both
``anki_miner-OLD.dist-info`` and ``anki_miner-NEW.dist-info`` in
``_internal/``; alphabetical filesystem enumeration picked the older one
and the app reported the wrong version. These tests guard the literal
source-of-truth invariant.
"""

import ast
import re
from pathlib import Path

import pytest

import anki_miner

INIT_PATH = Path(anki_miner.__file__)


def _parse_version_literal_with_ast() -> str:
    """Extract the literal RHS of ``__version__ = "..."`` via AST.

    Fails the test if the assignment isn't a plain string constant (i.e.
    if someone reintroduces a function call like ``version("anki-miner")``).
    """
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__version__":
                if not isinstance(node.value, ast.Constant):
                    pytest.fail(
                        "anki_miner.__version__ must be a plain string literal "
                        "(Issue #10). Got AST node: "
                        f"{type(node.value).__name__}. Do not reintroduce "
                        "importlib.metadata.version() — frozen builds can pick "
                        "up orphan dist-info from prior installs."
                    )
                if not isinstance(node.value.value, str):
                    pytest.fail(f"__version__ literal must be str, got {type(node.value.value).__name__}")
                return node.value.value
    pytest.fail("No __version__ assignment found in anki_miner/__init__.py")


def test_version_is_static_string_literal() -> None:
    """Runtime ``__version__`` must equal the literal in the source file."""
    literal = _parse_version_literal_with_ast()
    assert anki_miner.__version__ == literal, (
        f"Runtime __version__ ({anki_miner.__version__!r}) does not match "
        f"the source literal ({literal!r}). __version__ must be a hardcoded "
        f"string, not a computed value."
    )


def test_version_pep440_shape() -> None:
    """Version literal must be a sane PEP 440-ish dotted release."""
    assert re.match(r"^\d+\.\d+\.\d+([.-].+)?$", anki_miner.__version__), (
        f"__version__ {anki_miner.__version__!r} is not a recognizable " f"PEP 440 release string."
    )


def test_init_does_not_import_importlib_metadata() -> None:
    """Guard rail: __init__.py must not depend on filesystem metadata.

    Importing :mod:`importlib.metadata` in ``anki_miner/__init__.py`` is the
    exact regression path for Issue #10 — even if the call is wrapped in a
    try/except, the temptation to fall back to ``version("anki-miner")``
    reintroduces the orphan-dist-info bug.
    """
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("importlib.metadata"), (
                    f"anki_miner/__init__.py must not import {alias.name}. "
                    "See Issue #10 for why __version__ is a hardcoded literal."
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("importlib.metadata") and module != "importlib", (
                f"anki_miner/__init__.py must not import from {module!r}. "
                "See Issue #10 for why __version__ is a hardcoded literal."
            )

"""Static gate: ASR-dependency tests must be marked so they stay out of the
``[dev]``-only CI ``test`` job.

The default CI ``test`` job installs only ``.[dev]`` (no ``faster-whisper`` /
``numpy`` closure) and runs ``-m "not youtube and not e2e and not asr"``. A test
file that imports an asr-extra-only package without an ``asr`` (or ``e2e``)
marker therefore collects into that job and dies with
``ModuleNotFoundError: No module named 'numpy'`` — exactly the breakage that took
out CI on a docs-only commit when ``test_asr_transcriber`` /
``test_subtitle_gen_worker`` first landed unmarked.

This test is pure ``ast`` (it never imports the heavy packages), so it runs in
the ``[dev]`` job itself and turns that confusing runtime ``ModuleNotFoundError``
into an upfront "you forgot the marker" failure. It catches the leak regardless
of whether the developer's local venv happens to have numpy installed, which is
why ``scripts/health.sh`` alone cannot.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Top-level packages pulled in ONLY by the `[asr]` extra (faster-whisper's
# closure), confirmed absent from `[dev]` via requirements.lock. A test importing
# any of these needs the asr extra installed to run.
ASR_ONLY = frozenset(
    {
        "numpy",
        "faster_whisper",
        "ctranslate2",
        "av",
        "onnxruntime",
        "huggingface_hub",
        "tokenizers",
    }
)

# Markers that keep a file OUT of the `[dev]`-only default `test` job. Either is
# acceptable: both `asr` and `e2e` are negated in that job's `-m` filter.
EXEMPTING_MARKERS = ("asr", "e2e")

TESTS_ROOT = Path(__file__).resolve().parent.parent


def _imported_top_level_packages(tree: ast.AST) -> set[str]:
    """First path component of every import anywhere in the module (module-level
    or nested inside a function/helper — both reach the interpreter at runtime)."""
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                packages.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # node.level == 0 skips relative imports (no top-level package name)
            packages.add(node.module.split(".")[0])
    return packages


def _has_module_level_marker(tree: ast.Module) -> bool:
    """True if a module-level ``pytestmark`` assignment references an exempting
    mark (``pytest.mark.asr`` / ``pytest.mark.e2e``), single or in a list."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            continue
        rendered = ast.unparse(node.value)
        if any(f"mark.{mark}" in rendered for mark in EXEMPTING_MARKERS):
            return True
    return False


def _offenders() -> list[tuple[Path, set[str]]]:
    offenders: list[tuple[Path, set[str]]] = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        asr_imports = _imported_top_level_packages(tree) & ASR_ONLY
        if asr_imports and not _has_module_level_marker(tree):
            offenders.append((path, asr_imports))
    return offenders


def test_asr_dependency_tests_are_marked() -> None:
    offenders = _offenders()
    if offenders:
        lines = [
            f"  {path.relative_to(TESTS_ROOT.parent)} imports asr-only dep(s) "
            f"{sorted(deps)} but is missing `pytestmark = pytest.mark.asr`"
            for path, deps in offenders
        ]
        raise AssertionError(
            "Test files importing an `[asr]`-extra-only package must declare a "
            "module-level `pytestmark = pytest.mark.asr` (or `e2e`), else they run "
            "in the `[dev]`-only CI `test` job and fail with "
            "`ModuleNotFoundError: No module named 'numpy'`:\n" + "\n".join(lines)
        )

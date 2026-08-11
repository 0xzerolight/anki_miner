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
import sys
from pathlib import Path

import pytest

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


def _literal_importorskip_package(node: ast.Call) -> str | None:
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "importorskip"
        and isinstance(func.value, ast.Name)
        and func.value.id == "pytest"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return None
    return node.args[0].value.split(".")[0]


def _directly_imported_top_level_packages(tree: ast.AST) -> set[str]:
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            packages.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            packages.add(node.module.split(".")[0])
    return packages


def _imported_top_level_packages(tree: ast.AST) -> set[str]:
    """First path component of every import anywhere in the module (module-level
    or nested inside a function/helper — both reach the interpreter at runtime)."""
    packages: set[str] = set()
    packages.update(_directly_imported_top_level_packages(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (package := _literal_importorskip_package(node)):
            packages.add(package)
    return packages


def _decorators_have_exempting_marker(decorators: list[ast.expr]) -> bool:
    rendered = " ".join(ast.unparse(decorator) for decorator in decorators)
    return any(f"mark.{mark}" in rendered for mark in EXEMPTING_MARKERS)


def _unmarked_importorskip_packages(tree: ast.Module) -> set[str]:
    class ImportorskipVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.marked = False
            self.packages: set[str] = set()

        def _visit_scope(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            previous = self.marked
            self.marked = previous or _decorators_have_exempting_marker(node.decorator_list)
            for statement in node.body:
                self.visit(statement)
            self.marked = previous

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            self._visit_scope(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self._visit_scope(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self._visit_scope(node)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            package = _literal_importorskip_package(node)
            if package is not None and not self.marked:
                self.packages.add(package)
            self.generic_visit(node)

    visitor = ImportorskipVisitor()
    visitor.visit(tree)
    return visitor.packages


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
        if _has_module_level_marker(tree):
            continue
        asr_imports = (_directly_imported_top_level_packages(tree) | _unmarked_importorskip_packages(tree)) & ASR_ONLY
        if asr_imports:
            offenders.append((path, asr_imports))
    return offenders


def test_asr_dependency_tests_are_marked() -> None:
    offenders = _offenders()
    if offenders:
        lines = [
            f"  {path.relative_to(TESTS_ROOT.parent)} uses asr-only dep(s) "
            f"{sorted(deps)} without an effective `asr` or `e2e` marker"
            for path, deps in offenders
        ]
        raise AssertionError(
            "Tests using an `[asr]`-extra-only package need an effective `asr` or `e2e` marker; "
            "otherwise dependency-gated tests can skip or be deselected across the CI selections, "
            "leaving their behavior uncovered:\n" + "\n".join(lines)
        )


def test_literal_importorskip_is_an_asr_dependency() -> None:
    tree = ast.parse('def test_wave():\n    pytest.importorskip("numpy")\n')
    assert _imported_top_level_packages(tree) & ASR_ONLY == {"numpy"}


def test_importorskip_accepts_local_asr_marker(tmp_path, monkeypatch) -> None:
    (tmp_path / "test_marked.py").write_text(
        "import pytest\n"
        "@pytest.mark.asr\n"
        "class TestWave:\n"
        "    def test_load(self):\n"
        "        pytest.importorskip('numpy')\n",
        encoding="utf-8",
    )
    unmarked = tmp_path / "test_unmarked.py"
    unmarked.write_text(
        "import pytest\ndef test_load():\n    pytest.importorskip('numpy')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "TESTS_ROOT", tmp_path)

    assert _offenders() == [(unmarked, {"numpy"})]


def test_unmarked_importorskip_diagnostic_describes_coverage_loss(tmp_path, monkeypatch) -> None:
    unmarked = tmp_path / "test_unmarked.py"
    unmarked.write_text(
        "import pytest\ndef test_load():\n    pytest.importorskip('numpy')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "TESTS_ROOT", tmp_path)

    with pytest.raises(AssertionError) as exc_info:
        test_asr_dependency_tests_are_marked()

    message = str(exc_info.value)
    assert "effective `asr` or `e2e` marker" in message
    assert "skip or be deselected" in message
    assert "module-level" not in message
    assert "ModuleNotFoundError" not in message

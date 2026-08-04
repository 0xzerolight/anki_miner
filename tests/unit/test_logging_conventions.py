"""Repository-wide structural checks for package logging conventions."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_PACKAGE_ROOT = _ROOT / "anki_miner"
_WORKERS_ROOT = _PACKAGE_ROOT / "gui" / "workers"

_UNUSED_LOGGER_ALLOWLIST = frozenset(
    {
        # log_start/report_failure deliberately resolve the concrete subclass's module per instance.
        "anki_miner/gui/workers/base_worker.py",
    }
)

# Frozen. Do not grow: every new concrete worker must log its boundary start.
_WORKER_START_ALLOWLIST: frozenset[tuple[str, str]] = frozenset()


def _parse(path: Path) -> ast.Module:
    """Parse one Python module."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_logging_get_logger(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logging"
        and node.func.attr == "getLogger"
    )


def _defines_module_logger(tree: ast.Module) -> bool:
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and _is_logging_get_logger(statement.value)
            and any(isinstance(target, ast.Name) and target.id == "logger" for target in statement.targets)
        ):
            return True
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "logger"
            and statement.value is not None
            and _is_logging_get_logger(statement.value)
        ):
            return True
    return False


def _logger_reference_count(tree: ast.Module) -> int:
    return sum(isinstance(node, ast.Name) and node.id == "logger" for node in ast.walk(tree))


def _base_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _class_bases(node: ast.ClassDef) -> set[str]:
    return {name for base in node.bases if (name := _base_name(base)) is not None}


def _run_method(node: ast.ClassDef) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next(
        (
            statement
            for statement in node.body
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name == "run"
        ),
        None,
    )


def _is_concrete_worker(node: ast.ClassDef) -> bool:
    for statement in node.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(statement):
            if not isinstance(child, ast.Raise) or child.exc is None:
                continue
            raised = child.exc.func if isinstance(child.exc, ast.Call) else child.exc
            if isinstance(raised, ast.Name) and raised.id == "NotImplementedError":
                return False
    return True


def _calls_log_start(run: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and node.func.attr == "log_start"
        for node in ast.walk(run)
    )


def _worker_classes() -> dict[str, tuple[Path, ast.ClassDef]]:
    classes: dict[str, tuple[Path, ast.ClassDef]] = {}
    for path in sorted(_WORKERS_ROOT.glob("*.py")):
        tree = _parse(path)
        for statement in tree.body:
            if isinstance(statement, ast.ClassDef):
                classes[statement.name] = (path, statement)
    return classes


def _effective_run(
    worker: ast.ClassDef,
    classes: dict[str, tuple[Path, ast.ClassDef]],
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    if run := _run_method(worker):
        return run
    for base in _class_bases(worker):
        if base in classes and (run := _run_method(classes[base][1])):
            return run
    return None


def test_no_module_defines_an_unused_logger() -> None:
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        relative_path = path.relative_to(_ROOT).as_posix()
        tree = _parse(path)
        if not _defines_module_logger(tree) or relative_path in _UNUSED_LOGGER_ALLOWLIST:
            continue
        assert _logger_reference_count(tree) >= 2, f"{relative_path}: add a logger call or remove the unused logger"


def test_every_worker_logs_a_start_line() -> None:
    """Frozen allowlist may not grow; every new worker must log its start."""
    classes = _worker_classes()
    for class_name, (path, worker) in sorted(classes.items()):
        bases = _class_bases(worker)
        derives_from_cancellable = "CancellableWorker" in bases or any(
            base in classes and "CancellableWorker" in _class_bases(classes[base][1]) for base in bases
        )
        run = _effective_run(worker, classes)
        if not derives_from_cancellable or not _is_concrete_worker(worker) or run is None:
            continue
        relative_path = path.relative_to(_ROOT).as_posix()
        if (relative_path, class_name) in _WORKER_START_ALLOWLIST:
            continue
        assert _calls_log_start(run), f"{relative_path}:{class_name}: call self.log_start(...) at the top of run()"

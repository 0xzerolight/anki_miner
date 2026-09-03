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

# Frozen. Do not grow: every start receipt needs its closing end line, so an
# unclosed start keeps its single unambiguous reading — the worker never returned.
_WORKER_END_ALLOWLIST: frozenset[tuple[str, str]] = frozenset()


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


def _calls_self_method(run: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and node.func.attr == name
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
    _seen: frozenset[str] = frozenset(),
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The ``run`` this class executes: its own, else the nearest inherited one.

    Walks the whole base chain rather than one level: the queue workers reach
    ``SequentialQueueWorker`` through ``ProcessorOwningWorker``, and a one-level
    lookup found nothing for them and silently skipped the check.
    """
    if run := _run_method(worker):
        return run
    for base in _class_bases(worker) - _seen:
        if base in classes and (run := _effective_run(classes[base][1], classes, _seen | {base})):
            return run
    return None


def _cancellable_workers(classes: dict[str, tuple[Path, ast.ClassDef]]) -> set[str]:
    """Every class deriving from ``CancellableWorker``, however deep.

    A fixpoint over the parsed classes, not a one-level base check:
    ``AudiobookQueueWorker`` reaches ``CancellableWorker`` via
    ``SequentialQueueWorker`` -> ``ProcessorOwningWorker``, so the one-level
    rule excused exactly the workers that run longest.
    """
    derived = {"CancellableWorker"}
    changed = True
    while changed:
        changed = False
        for name, (_path, node) in classes.items():
            if name not in derived and _class_bases(node) & derived:
                derived.add(name)
                changed = True
    return derived - {"CancellableWorker"}


def _ratcheted_workers(
    classes: dict[str, tuple[Path, ast.ClassDef]],
) -> list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """``(relative path, class name, effective run)`` per concrete worker."""
    derived = _cancellable_workers(classes)
    workers: list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for class_name, (path, worker) in sorted(classes.items()):
        if class_name not in derived or not _is_concrete_worker(worker):
            continue
        run = _effective_run(worker, classes)
        if run is None:
            continue
        workers.append((path.relative_to(_ROOT).as_posix(), class_name, run))
    return workers


def test_no_module_defines_an_unused_logger() -> None:
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        relative_path = path.relative_to(_ROOT).as_posix()
        tree = _parse(path)
        if not _defines_module_logger(tree) or relative_path in _UNUSED_LOGGER_ALLOWLIST:
            continue
        assert _logger_reference_count(tree) >= 2, f"{relative_path}: add a logger call or remove the unused logger"


def test_every_worker_logs_a_start_line() -> None:
    """Frozen allowlist may not grow; every new worker must log its start."""
    for relative_path, class_name, run in _ratcheted_workers(_worker_classes()):
        if (relative_path, class_name) in _WORKER_START_ALLOWLIST:
            continue
        assert _calls_self_method(
            run, "log_start"
        ), f"{relative_path}:{class_name}: call self.log_start(...) at the top of run()"


def test_every_worker_logs_an_end_line() -> None:
    """Frozen allowlist may not grow; every worker must close its start receipt."""
    for relative_path, class_name, run in _ratcheted_workers(_worker_classes()):
        if (relative_path, class_name) in _WORKER_END_ALLOWLIST:
            continue
        assert _calls_self_method(run, "log_end") or _calls_self_method(
            run, "report_failure"
        ), f"{relative_path}:{class_name}: close run() with self.log_end(...) or self.report_failure(...)"

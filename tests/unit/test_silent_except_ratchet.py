"""Ratchet: broad exception handlers that swallow a failure without logging it.

Every motivating bug of the logging overhaul had the same shape — a failure was
caught by a broad handler, the handler did nothing, and the log said nothing. The
budget in ``silent_except_budget.txt`` records the sites that survived the sweep;
it may shrink and never grow.

Counted, per file:

* ``except Exception``/``except BaseException``/bare ``except`` whose body only
  ``pass``/``return``/``continue``/``break``\\ s (a docstring or ``...`` is body
  filler, not a body), and
* ``with suppress(Exception)`` / ``contextlib.suppress(BaseException)`` blocks

in both cases only when nothing inside logs. ``utils.logging_ext.suppressed`` is
the logged replacement for ``suppress`` and is never counted. Narrow handlers
(``except OSError: pass``) are a deliberate decision about a known failure and
are not counted either.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_PACKAGE_ROOT = _ROOT / "anki_miner"
_BUDGET_FILE = Path(__file__).with_name("silent_except_budget.txt")

_BROAD_EXCEPTIONS = frozenset({"Exception", "BaseException"})
_LOGGER_OBJECTS = frozenset({"logger", "log", "_log", "logging", "self"})
_LOGGING_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})
_LOGGING_FUNCTIONS = frozenset({"log_summary", "suppressed", "report_failure", "log_command", "log_command_result"})

_FIX_HINT = "log it via logger.warning / log_summary / utils.logging_ext.suppressed, or lower the budget"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _logs_anything(body: list[ast.stmt]) -> bool:
    """True when any call in ``body`` is a logging call."""
    for statement in body:
        for node in ast.walk(statement):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in _LOGGING_FUNCTIONS:
                return True
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr in _LOGGING_FUNCTIONS:
                return True
            if func.attr in _LOGGING_METHODS and isinstance(func.value, ast.Name) and func.value.id in _LOGGER_OBJECTS:
                return True
    return False


def _is_body_filler(statement: ast.stmt) -> bool:
    """``pass``/``return``/``continue``/``break``, a docstring or ``...``."""
    if isinstance(statement, (ast.Pass, ast.Continue, ast.Break)):
        return True
    if isinstance(statement, ast.Return) and (statement.value is None or isinstance(statement.value, ast.Constant)):
        return True
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    if caught is None:  # bare except
        return True
    names = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return any(isinstance(name, ast.Name) and name.id in _BROAD_EXCEPTIONS for name in names)


def _suppresses_broadly(item: ast.withitem) -> bool:
    call = item.context_expr
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    if name != "suppress":
        return False
    for argument in call.args:
        caught = (
            argument.attr
            if isinstance(argument, ast.Attribute)
            else argument.id if isinstance(argument, ast.Name) else None
        )
        if caught in _BROAD_EXCEPTIONS:
            return True
    return False


def _silent_sites(tree: ast.Module) -> list[int]:
    """Line numbers of the silent broad swallow sites in one module."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            silent = _is_broad_handler(node) and all(map(_is_body_filler, node.body))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            silent = any(map(_suppresses_broadly, node.items))
        else:
            continue
        if silent and not _logs_anything(node.body):
            lines.append(node.lineno)
    return sorted(lines)


def measure_silent_sites() -> dict[str, list[int]]:
    """``{relative path: [line, ...]}`` for every silent broad swallow site."""
    found: dict[str, list[int]] = {}
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        if lines := _silent_sites(_parse(path)):
            found[path.relative_to(_ROOT).as_posix()] = lines
    return found


def read_budget(section: str = "") -> dict[str, int]:
    """Parse ``silent_except_budget.txt``.

    ``section=""`` reads the per-file counts; ``section="subprocess"`` reads the
    ``subprocess:``-prefixed module list (values are always ``0`` there — the
    section is a membership list, not a count).
    """
    budget: dict[str, int] = {}
    for raw in _BUDGET_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        prefix, marker, rest = line.partition(":")
        if marker:
            if prefix.strip() == section:
                budget[rest.strip()] = 0
            continue
        if section:
            continue
        relative_path, _tab, count = line.partition("\t")
        budget[relative_path.strip()] = int(count.strip())
    return budget


def test_no_new_silent_broad_except_handlers() -> None:
    """The per-file budget may shrink and never grow."""
    measured = measure_silent_sites()
    budget = read_budget()

    over = [
        f"{path}:{','.join(map(str, lines))} — {len(lines)} silent site(s), budget {budget.get(path, 0)}"
        for path, lines in sorted(measured.items())
        if len(lines) > budget.get(path, 0)
    ]
    assert not over, "new silent broad exception handler(s):\n  " + "\n  ".join(over) + f"\n{_FIX_HINT}"

    stale = sorted(path for path in budget if path not in measured)
    assert not stale, (
        "budget entries with no remaining silent site — delete these lines from "
        f"{_BUDGET_FILE.name} so the ratchet stays tight:\n  " + "\n  ".join(stale)
    )


def test_the_ratchet_sees_a_silent_handler() -> None:
    """The detector itself, on a module that has one of each shape."""
    tree = ast.parse(
        "import contextlib\n"
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        pass\n"
        "    with contextlib.suppress(Exception):\n"
        "        g()\n"
        "    try:\n"
        "        g()\n"
        "    except OSError:\n"
        "        pass\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        logger.warning('x')\n"
        "    with contextlib.suppress(Exception):\n"
        "        logger.debug('x')\n"
    )
    assert _silent_sites(tree) == [5, 7]

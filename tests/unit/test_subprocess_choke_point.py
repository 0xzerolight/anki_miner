"""Ratchet: child processes are spawned through the logged helper.

``utils.process_supervisor.run_supervised`` is the choke point that logs argv,
exit state, elapsed time and the stderr tail. A module that calls
``subprocess.run``/``Popen``/``check_output``/``check_call`` itself spawns a
child whose failure leaves nothing in the log but the caller's own message.

Three modules own the spawn and are exempt: the supervisor itself, the Windows
console-window kwargs helper, and the diagnostics probe. Everything else must be
listed in the ``subprocess:`` section of ``silent_except_budget.txt`` — that list
drains as the remaining call sites move onto the supervisor, and a module that
is not on it fails this test.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit.test_silent_except_ratchet import read_budget

_ROOT = Path(__file__).parents[2]
_PACKAGE_ROOT = _ROOT / "anki_miner"

_SUBPROCESS_HELPER_MODULES = frozenset(
    {
        "anki_miner/utils/process_supervisor.py",
        "anki_miner/utils/subprocess_utils.py",
        "anki_miner/diagnostics/environment.py",
    }
)

_SPAWNING_CALLS = frozenset({"run", "Popen", "call", "check_call", "check_output"})

_FIX_HINT = (
    "spawn through anki_miner.utils.process_supervisor.run_supervised(..., op=...) so argv, "
    "exit state and the stderr tail reach the log"
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_spawners(tree: ast.Module) -> set[str]:
    """Names bound by ``from subprocess import run, Popen`` (aliases included)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            names |= {alias.asname or alias.name for alias in node.names if alias.name in _SPAWNING_CALLS}
    return names


def spawn_sites(tree: ast.Module) -> list[str]:
    """``"<call>@<line>"`` for every direct subprocess spawn in one module."""
    bound = _imported_spawners(tree)
    sites: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in _SPAWNING_CALLS and isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                sites.append(f"subprocess.{func.attr}@{node.lineno}")
        elif isinstance(func, ast.Name) and func.id in bound:
            sites.append(f"{func.id}@{node.lineno}")
    return sites


def measure_spawn_sites() -> dict[str, list[str]]:
    """``{relative path: ["<call>@<line>", ...]}`` outside the helper modules."""
    found: dict[str, list[str]] = {}
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        relative_path = path.relative_to(_ROOT).as_posix()
        if relative_path in _SUBPROCESS_HELPER_MODULES:
            continue
        if sites := spawn_sites(_parse(path)):
            found[relative_path] = sites
    return found


def test_subprocess_calls_go_through_the_logged_helper() -> None:
    """No module may start spawning children on its own."""
    measured = measure_spawn_sites()
    listed = read_budget("subprocess")

    unlisted = [f"{path}: {', '.join(sites)}" for path, sites in sorted(measured.items()) if path not in listed]
    assert not unlisted, (
        "subprocess call site(s) outside the logged helper:\n  " + "\n  ".join(unlisted) + f"\n{_FIX_HINT}"
    )

    stale = sorted(path for path in listed if path not in measured)
    assert not stale, (
        "subprocess: entries with no remaining call site — delete these lines from "
        "silent_except_budget.txt so the ratchet stays tight:\n  " + "\n  ".join(stale)
    )


def test_the_helper_modules_are_the_only_exemptions() -> None:
    """The exemption list stays honest: each exempt module really does spawn or wrap one."""
    for relative_path in sorted(_SUBPROCESS_HELPER_MODULES):
        path = _ROOT / relative_path
        assert path.exists(), f"{relative_path}: exempt module no longer exists"
        source = path.read_text(encoding="utf-8")
        assert "import subprocess" in source, f"{relative_path}: no longer uses subprocess; drop the exemption"


def test_the_ratchet_sees_a_direct_spawn() -> None:
    """The detector itself, on both import shapes."""
    tree = ast.parse(
        "import subprocess\n"
        "from subprocess import Popen as _Popen\n"
        "def f():\n"
        "    subprocess.run(['x'])\n"
        "    _Popen(['x'])\n"
        "    run_supervised(['x'])\n"
        "    subprocess.PIPE\n"
    )
    assert spawn_sites(tree) == ["subprocess.run@4", "_Popen@5"]

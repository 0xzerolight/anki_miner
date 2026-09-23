"""``pylupdate6`` needs a LITERAL context, or it drops the string in silence.

``QCoreApplication.translate(_CTX, "Switch mining language")`` reads perfectly
and works at runtime -- the lookup just misses, so English comes back. What it
does not do is get extracted: ``pylupdate6`` parses the source, it does not run
it, so a context passed as a name is a call it cannot classify and skips. The
string then never reaches ``en.ts``, never reaches a translator, and the
catalogue-completeness check stays green because it compares the catalogues
against that same incomplete ``en.ts``.

Eleven strings went missing this way in one module before anyone noticed. This
test is the thing that notices.

The rule is narrower than "context must be a literal": a call whose TEXT is a
name is a deliberate runtime lookup of a string registered elsewhere with
``QT_TRANSLATE_NOOP`` (``gui/capabilities.py``, ``gui/widgets/base/queue_row.py``),
and those registrations carry the literal. Only a LITERAL text with a
non-literal context is the silent drop.
"""

from __future__ import annotations

import ast
from pathlib import Path

GUI_ROOT = Path(__file__).resolve().parents[2] / "anki_miner" / "gui"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The two call shapes ``pylupdate6`` extracts from, both context-first.
_EXTRACTED_CALLS = frozenset({"translate", "QT_TRANSLATE_NOOP"})


def _is_str_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def extraction_sites() -> list[tuple[str, int, bool, bool]]:
    """``(relpath, lineno, context_is_literal, text_is_literal)`` for every call."""
    found: list[tuple[str, int, bool, bool]] = []
    for path in sorted(GUI_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "translate" not in source and "QT_TRANSLATE_NOOP" not in source:
            continue
        relpath = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            if _call_name(node.func) not in _EXTRACTED_CALLS:
                continue
            found.append((relpath, node.lineno, _is_str_literal(node.args[0]), _is_str_literal(node.args[1])))
    return found


def test_the_scan_actually_finds_the_call_sites():
    """A guard whose parser silently matched nothing would always pass."""
    assert len(extraction_sites()) > 100


def test_every_extractable_string_has_a_literal_context():
    dropped = sorted(
        f"{relpath}:{lineno}"
        for relpath, lineno, context_literal, text_literal in extraction_sites()
        if text_literal and not context_literal
    )
    assert dropped == [], (
        "pylupdate6 drops these strings: the context must be a string literal at "
        "the call, not a module constant. Spell the context out."
    )


def _wrapper_literal_call_sites() -> list[str]:
    """Sites calling a local ``_tr``-style wrapper with a bare string literal.

    A wrapper whose own ``translate``/``QT_TRANSLATE_NOOP`` call passes its text
    parameter through unchanged (e.g. ``_tr`` in ``capability_browser.py``,
    ``frequency_import_flow.py``, ``pitch_import_flow.py``,
    ``progress_telemetry.py``, ``service_factory.py``) is invisible to
    ``pylupdate6`` -- neither argument at that inner call is a literal. Calling
    it with a literal at the *use* site (not a registry value declared with
    ``QT_TRANSLATE_NOOP`` elsewhere) is therefore the same silent drop.
    """
    dropped: list[str] = []
    for path in sorted(GUI_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "translate" not in source and "QT_TRANSLATE_NOOP" not in source:
            continue
        tree = ast.parse(source)
        relpath = path.relative_to(REPO_ROOT).as_posix()
        wrapper_names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = {a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call) or len(sub.args) < 2 or _call_name(sub.func) not in _EXTRACTED_CALLS:
                    continue
                text = sub.args[1]
                if isinstance(text, ast.Name) and text.id in params:
                    wrapper_names.add(node.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id in wrapper_names and node.args and _is_str_literal(node.args[0]):
                dropped.append(f"{relpath}:{node.lineno}")
    return dropped


def test_tr_wrapper_helpers_reject_bare_literal_arguments():
    dropped = _wrapper_literal_call_sites()
    assert dropped == [], (
        "these call a local translate()-wrapper (e.g. _tr) with a bare string "
        "literal, which pylupdate6 cannot see through: call "
        "QCoreApplication.translate('Context', '...') directly instead. "
        f"Sites: {dropped}"
    )

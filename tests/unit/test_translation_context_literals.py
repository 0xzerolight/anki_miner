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

"""S18: the frozen bundle pins the shared engine packs' manifests (text-parse test).

Mould: ``test_zh_bundling.py``. PyInstaller is not in the dev venv, so the
spec's shared-pack block is lifted out by AST and executed on its own.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import SimpleNamespace

from anki_miner.languages import SHARED_PACK_CODES

SPEC = Path(__file__).resolve().parents[3] / "anki_miner.spec"


def _run_block(namespace: dict[str, object]) -> list[str]:
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    block = [node for node in tree.body if "_shared_pack_hiddenimports" in ast.dump(node)]
    assert len(block) == 3, "the spec's shared-pack block (declaration, loop, splice) is missing"
    namespace.setdefault("language_hiddenimports", [])
    exec(compile(ast.Module(body=block, type_ignores=[]), str(SPEC), "exec"), namespace)  # noqa: S102
    return namespace["language_hiddenimports"]  # type: ignore[return-value]


def test_the_spec_imports_the_shared_codes():
    assert "from anki_miner.languages import AVAILABLE_LANGUAGES, SHARED_PACK_CODES" in SPEC.read_text(encoding="utf-8")


def test_an_existing_shared_package_pins_its_package_and_manifest():
    present = "anki_miner.languages._fakepack"
    fake_importlib = SimpleNamespace(util=SimpleNamespace(find_spec=lambda name: object() if name == present else None))

    pins = _run_block(
        {"SHARED_PACK_CODES": ("_fakepack", "_absent"), "importlib": fake_importlib, "language_hiddenimports": ["x"]}
    )

    assert pins == ["x", present, f"{present}.pack"]


def test_the_real_codes_evaluate_without_raising():
    pins = _run_block({"SHARED_PACK_CODES": SHARED_PACK_CODES, "importlib": importlib})
    for code in SHARED_PACK_CODES:
        package = f"anki_miner.languages.{code}"
        assert (package in pins) is (importlib.util.find_spec(package) is not None)


#: Stdlib modules the _spacy engine pack imports at package load that nothing in
#: the base graph reaches once spaCy and its dependencies are excluded. Each one
#: missing was a ModuleNotFoundError for every spaCy language in the frozen app:
#: timeit (spacy/language.py), cProfile + pstats (spacy/cli/profile.py, which
#: spacy/__init__ reaches through spacy.cli), zoneinfo (pydantic/_internal).
SPACY_PACK_STDLIB = ("timeit", "cProfile", "pstats", "zoneinfo")


def test_the_stdlib_modules_the_spacy_pack_needs_from_the_base_stay_pinned():
    from tests.unit.languages.test_zh_bundling import _list_body

    hiddenimports = _list_body("hiddenimports")
    for name in SPACY_PACK_STDLIB:
        assert f'"{name}",' in hiddenimports, f"anki_miner.spec no longer pins {name} for the spaCy engine pack"

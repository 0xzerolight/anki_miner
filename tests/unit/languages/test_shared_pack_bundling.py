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

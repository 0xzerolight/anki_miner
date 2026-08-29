"""The zh PyInstaller hooks collect the right packages.

PyInstaller is a build-time tool and is NOT installed in this venv (it is absent
from every extra in pyproject.toml), so importing a hook module directly would
raise ModuleNotFoundError. Each hook is exec'd against a recording stub of
``PyInstaller.utils.hooks`` installed in ``sys.modules`` — ``from a.b.c import
x`` short-circuits on a sys.modules hit, so the real hook body runs and records
the package names it asks for, which a substring assertion could not prove. The
stub is installed unconditionally, so a machine that does have PyInstaller
behaves identically.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HOOKS_DIR = PROJECT_ROOT / "PyInstaller-Hooks"


def _load_hook(name: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []
    hooks = types.ModuleType("PyInstaller.utils.hooks")

    def collect_data_files(package: str, **_kwargs: Any) -> list[tuple[str, str]]:
        calls.append(("collect_data_files", package))
        return [(f"/site/{package}/data.bin", package)]

    def collect_submodules(package: str, **_kwargs: Any) -> list[str]:
        calls.append(("collect_submodules", package))
        return [package, f"{package}.sub"]

    def collect_all(package: str, **_kwargs: Any) -> tuple[list, list, list]:
        calls.append(("collect_all", package))
        return ([(f"/site/{package}/d", package)], [(f"/site/{package}/lib.so", package)], [package])

    hooks.collect_data_files = collect_data_files
    hooks.collect_submodules = collect_submodules
    hooks.collect_all = collect_all
    package = types.ModuleType("PyInstaller")
    utils = types.ModuleType("PyInstaller.utils")
    utils.hooks = hooks
    package.utils = utils
    monkeypatch.setitem(sys.modules, "PyInstaller", package)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils", utils)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)

    path = HOOKS_DIR / f"hook-{name}.py"
    assert path.is_file(), f"missing hook: {path}"
    spec = importlib.util.spec_from_file_location(f"_probe_hook_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def test_jieba_hook_collects_dict_data_and_submodules(monkeypatch: pytest.MonkeyPatch) -> None:
    module, calls = _load_hook("jieba", monkeypatch)
    assert ("collect_data_files", "jieba") in calls
    assert ("collect_submodules", "jieba") in calls
    assert module.datas
    assert "jieba.posseg" in module.hiddenimports


def test_pypinyin_hook_collects_phrase_data(monkeypatch: pytest.MonkeyPatch) -> None:
    module, calls = _load_hook("pypinyin", monkeypatch)
    assert ("collect_data_files", "pypinyin") in calls
    assert ("collect_submodules", "pypinyin") in calls
    assert module.datas


def test_opencc_hook_collects_native_lib_and_dictionaries(monkeypatch: pytest.MonkeyPatch) -> None:
    module, calls = _load_hook("opencc", monkeypatch)
    assert ("collect_all", "opencc") in calls
    assert module.binaries
    assert module.datas


def test_spec_pins_the_zh_packages_into_the_import_graph() -> None:
    spec_text = (PROJECT_ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    for entry in (
        '"jieba.posseg"',
        '"pypinyin"',
        '"opencc"',
        # tagger_provider resolves this module through an f-string importlib
        # call bytecode analysis cannot follow; the 2B dry-run shipped a bundle
        # without it and the zh smoke died on "No tokenizer registered".
        '"anki_miner.languages.zh.tokenizer"',
        # Same f-string importlib blind spot for the Korean engine.
        '"anki_miner.languages.ko.tokenizer"',
    ):
        assert entry in spec_text, f"anki_miner.spec does not pin {entry}"

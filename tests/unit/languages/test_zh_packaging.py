"""The zh extra is declared, type-ignored, and installed by CI and every release leg."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ZH_PACKAGES = ("jieba", "pypinyin", "opencc")


def _pyproject() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_zh_extra_names_every_runtime_package() -> None:
    extras = _pyproject()["project"]["optional-dependencies"]
    declared = " ".join(extras["zh"])
    for package in ZH_PACKAGES:
        assert package in declared, f"{package} missing from the zh extra"
    assert "anki-miner[zh]" in extras["languages"]


def test_mypy_ignores_untyped_zh_packages() -> None:
    overrides = _pyproject()["tool"]["mypy"]["overrides"]
    ignored = {module for entry in overrides if entry.get("ignore_missing_imports") for module in entry["module"]}
    for package in ZH_PACKAGES:
        assert f"{package}.*" in ignored


def test_ci_test_job_installs_the_zh_extra() -> None:
    ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'pip install -e ".[dev,zh]"' in ci


def test_every_release_leg_bundles_the_zh_extra() -> None:
    matrix = json.loads((PROJECT_ROOT / ".github" / "release-matrix.json").read_text(encoding="utf-8"))
    assert matrix
    for leg in matrix:
        assert "zh" in leg["install_target"], f"{leg['platform']} ships no zh engine"

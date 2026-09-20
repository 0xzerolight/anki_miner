"""The Cantonese engine ships as an extra and a pack, never as bundle content."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[3]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
SPEC = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")


def test_the_extra_pins_the_engine_and_joins_the_aggregate():
    extras = PYPROJECT["project"]["optional-dependencies"]
    assert extras["yue"] == ["pycantonese>=5.0,<6"]
    assert "anki-miner[yue]" in extras["languages"]


def test_mypy_ignores_both_engine_packages():
    modules = {name for o in PYPROJECT["tool"]["mypy"]["overrides"] for name in o.get("module", ())}
    assert {"pycantonese.*", "rustling.*"} <= modules


def test_both_engines_are_excluded_from_the_bundle():
    excludes = SPEC.split("excludes=[", 1)[1]
    assert '"pycantonese",' in excludes
    assert '"rustling",' in excludes


def test_no_licence_block_is_owed():
    # MIT engines, and the GPL-3 CantoMap data is in the pack exclude, so
    # nothing yue redistributes needs a notice directory (jieba, pypinyin and
    # pythainlp set the same precedent).
    assert "yue_license_datas" not in SPEC
    assert not (ROOT / "licenses" / "pycantonese").exists()
    assert not (ROOT / "licenses" / "rustling").exists()

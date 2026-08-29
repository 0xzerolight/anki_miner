"""The ko extra and its typing override are declared in pyproject."""

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _data() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_ko_extra_pins_kiwipiepy_and_its_model():
    extras = _data()["project"]["optional-dependencies"]
    assert extras["ko"] == ["kiwipiepy>=0.20", "kiwipiepy-model>=0.20"]


def test_languages_extra_aggregates_zh_and_ko():
    extras = _data()["project"]["optional-dependencies"]
    assert set(extras["languages"]) == {"anki-miner[zh]", "anki-miner[ko]"}


def test_mypy_ignores_missing_kiwipiepy_imports():
    overrides = _data()["tool"]["mypy"]["overrides"]
    modules = {m for o in overrides if o.get("ignore_missing_imports") for m in o["module"]}
    assert "kiwipiepy.*" in modules

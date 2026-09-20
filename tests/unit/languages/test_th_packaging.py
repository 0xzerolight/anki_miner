"""The th extra and its typing override are declared in pyproject."""

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _data() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_th_extra_pins_pythainlp():
    assert _data()["project"]["optional-dependencies"]["th"] == ["pythainlp>=5.3.7,<6"]


def test_languages_extra_aggregates_th():
    assert "anki-miner[th]" in _data()["project"]["optional-dependencies"]["languages"]


def test_mypy_ignores_missing_pythainlp_imports():
    overrides = _data()["tool"]["mypy"]["overrides"]
    modules = {m for o in overrides if o.get("ignore_missing_imports") for m in o["module"]}
    assert "pythainlp.*" in modules


def test_the_release_workflow_seeds_and_smokes_thai():
    """The pack seed list and the bundle-smoke language list both carry th.

    The seed list is what fetches the th pack into BUNDLE_SMOKE_PACK_SEEDS, and
    BUNDLE_SMOKE_LANGS is what makes the bundled smoke actually mine a Thai
    line; seeding without smoking would download the pack and never use it.
    """
    workflow = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    seed_lines = [line for line in workflow.splitlines() if "fetch_language_pack_seeds" in line]
    assert seed_lines, "release.yml no longer seeds language packs"
    assert any(" th" in line for line in seed_lines), seed_lines
    smoke_lines = [line for line in workflow.splitlines() if line.strip().startswith("BUNDLE_SMOKE_LANGS:")]
    assert smoke_lines, "release.yml no longer declares BUNDLE_SMOKE_LANGS"
    assert any("th" in line.split(":", 1)[1].split() for line in smoke_lines), smoke_lines

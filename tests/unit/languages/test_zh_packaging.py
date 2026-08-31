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
    # Since the ko fixtures landed, the test job installs the `languages`
    # aggregate rather than `zh` alone — the zh engine still arrives, via
    # `anki-miner[zh]` (pinned by test_zh_extra_names_every_runtime_package),
    # and the ko engine arrives with it so no fixture is silently skipped.
    ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'pip install -e ".[dev,languages]"' in ci


#: The versions the project's own venv resolved, pinned so a release bundle
#: cannot float onto a jieba/pypinyin/OpenCC the tokenizer was never run against.
ZH_LOCK_PINS = ("jieba==0.42.1", "pypinyin==0.55.0", "opencc==1.4.2")


def test_the_lock_pins_the_zh_engine() -> None:
    lock = (PROJECT_ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
    for pin in ZH_LOCK_PINS:
        assert pin in lock, f"{pin} missing from requirements.lock"


#: Same for the Korean engine: the lock's regeneration recipe installs `.[asr]`,
#: so kiwipiepy needs a pinned block of its own or a bundle floats onto a
#: tokenizer the Sejong POS tables were never run against.
KO_LOCK_PINS = ("kiwipiepy==0.23.2", "kiwipiepy-model==0.23.0")


def test_the_lock_pins_the_ko_engine() -> None:
    lock = (PROJECT_ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
    for pin in KO_LOCK_PINS:
        assert pin in lock, f"{pin} missing from requirements.lock"


def test_the_release_preflight_builds_against_the_zh_extra() -> None:
    """The preflight venv must carry the engine the zh pack delivers.

    Not what the release legs install (.[asr]) — the point is the opposite: the
    exclude that keeps jieba out of the frozen graph is only provable on a build
    where jieba is installed.
    """
    preflight = (PROJECT_ROOT / "scripts" / "release_preflight.sh").read_text(encoding="utf-8")
    assert '".[asr,zh,ko]"' in preflight
    assert '".[asr]"' not in preflight


def test_no_release_leg_installs_the_zh_extra() -> None:
    """The engines are excluded from the frozen graph and arrive as packs.

    The pip extras stay (a source install still gets a working zh), but a release
    leg installing them would only slow the build; the bundle smokes are handed
    real engines by ``scripts/fetch_language_pack_seeds.py`` instead.
    """
    matrix = json.loads((PROJECT_ROOT / ".github" / "release-matrix.json").read_text(encoding="utf-8"))
    assert matrix
    for leg in matrix:
        assert "zh" not in leg["install_target"], f"{leg['platform']} still installs the zh engine"

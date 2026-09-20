"""Arabic packaging: the notices ship, the release seeds and smokes ar, CI seeds the DB for the real-data tests."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'
CAMEL_NOTICE = ("licenses/camel-tools/LICENSE", "licenses/camel-tools/README.md")
CALIMA_LICENSE_SHA256 = "7a71f85f07e46759dcc1c16dba64352d9932447759cf71ca3deae3729d85d23e"


def test_the_release_workflow_seeds_and_smokes_arabic():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "ar" in codes and codes.index("ar") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("ar" in group for group in requested)


def test_ci_seeds_the_database_before_the_tests_that_read_it():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    test_job = workflow.split("\n  test:\n", 1)[1].split("\n  wheel-assets:\n", 1)[0]
    seed = test_job.index('fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds" ar')
    assert seed < test_job.index("pytest -m")
    assert "ANKI_MINER_TEST_PACK_SEEDS: ${{ runner.temp }}/lang_pack_seeds" in test_job


def test_the_ported_code_notice_ships_in_the_wheel():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert set(CAMEL_NOTICE) <= set(project["license-files"])
    wheel_gate = (ROOT / "scripts" / "check_wheel_assets.py").read_text(encoding="utf-8")
    required = wheel_gate.split("REQUIRED_WHEEL_LICENSES = [", 1)[1].split("]", 1)[0]
    assert all(f'"{path}"' in required for path in CAMEL_NOTICE)


def test_the_spec_ships_both_notices():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "camel-tools"' in spec and "+ camel_tools_license_datas" in spec
    assert '"licenses", "calima-msa-r13"' in spec and "+ calima_msa_r13_license_datas" in spec


def test_the_database_notice_is_the_zips_own_licence():
    notice = ROOT / "licenses" / "calima-msa-r13"
    assert sorted(path.name for path in notice.iterdir()) == ["LICENSE", "README.md", "SOURCES.txt"]
    assert hashlib.sha256((notice / "LICENSE").read_bytes()).hexdigest() == CALIMA_LICENSE_SHA256
    assert "anki_miner/languages/ar/pack.py" in (notice / "README.md").read_text(encoding="utf-8")

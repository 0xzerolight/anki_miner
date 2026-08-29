"""kiwipiepy hooks, spec wiring, LGPL notices and the release install target."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_licenses_dir_carries_lgpl_text_and_source_pointer():
    lic = ROOT / "licenses" / "kiwipiepy"
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in (lic / "COPYING.LGPLv3").read_text(encoding="utf-8")
    assert "github.com/bab2min/kiwipiepy" in (lic / "SOURCES.txt").read_text(encoding="utf-8")


def test_spec_wires_the_kiwipiepy_license_datas():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "kiwipiepy"' in spec
    assert "+ kiwipiepy_license_datas" in spec
    assert '"kiwipiepy_model",' in spec


def test_hooks_exist_and_are_find_spec_gated():
    for name in ("hook-kiwipiepy.py", "hook-kiwipiepy_model.py"):
        text = (ROOT / "PyInstaller-Hooks" / name).read_text(encoding="utf-8")
        assert "find_spec" in text
        assert "datas" in text


def test_every_release_matrix_leg_installs_the_ko_engine():
    matrix = json.loads((ROOT / ".github" / "release-matrix.json").read_text(encoding="utf-8"))
    assert matrix
    for entry in matrix:
        assert "ko" in entry["install_target"], entry["platform"]


def test_the_release_preflight_builds_against_the_ko_extra():
    """The preflight venv must carry both optional engines the release bundles."""
    preflight = (ROOT / "scripts" / "release_preflight.sh").read_text(encoding="utf-8")
    assert '".[asr,zh,ko]"' in preflight

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


def test_the_model_is_excluded_from_the_graph_not_pinned_into_it():
    """The ~88 MB model ships as an in-app download pack, never in the bundle.

    kiwipiepy's native loader imports ``kiwipiepy_model`` itself, which bytecode
    analysis cannot see but a hook or a stray transitive pull could still drag in
    — the Analysis exclude is what makes its absence a guarantee.
    """
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    hiddenimports, _, excludes = spec.partition("excludes=[")

    assert '"kiwipiepy_model"' not in hiddenimports
    assert '"kiwipiepy_model",' in excludes


def test_the_engine_hook_survives_and_the_model_hook_is_gone():
    text = (ROOT / "PyInstaller-Hooks" / "hook-kiwipiepy.py").read_text(encoding="utf-8")
    assert "find_spec" in text
    assert "datas" in text
    assert not (ROOT / "PyInstaller-Hooks" / "hook-kiwipiepy_model.py").exists()


def test_every_release_matrix_leg_installs_the_ko_engine():
    matrix = json.loads((ROOT / ".github" / "release-matrix.json").read_text(encoding="utf-8"))
    assert matrix
    for entry in matrix:
        assert "ko" in entry["install_target"], entry["platform"]


def test_the_release_preflight_builds_against_the_ko_extra():
    """The preflight venv must carry both optional engines the release bundles."""
    preflight = (ROOT / "scripts" / "release_preflight.sh").read_text(encoding="utf-8")
    assert '".[asr,zh,ko]"' in preflight

"""kiwipiepy spec wiring, LGPL notices and the release install target.

The engine itself is no longer bundled — it arrives as a language pack — so what
is pinned here is the notice that must travel with it and the absence of every
collecting hook. The generic exclude/hiddenimport assertions live in
``test_zh_bundling.py``.
"""

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


def test_the_engine_and_model_are_excluded_from_the_graph_not_pinned_into_it():
    """Both halves ship as an in-app download pack, never in the bundle.

    kiwipiepy's native loader imports ``kiwipiepy_model`` itself, which bytecode
    analysis cannot see but a hook or a stray transitive pull could still drag in
    — the Analysis exclude is what makes its absence a guarantee.
    """
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    _hiddenimports, _, excludes = spec.partition("excludes=[")

    assert '"kiwipiepy",' in excludes
    assert '"kiwipiepy_model",' in excludes


def test_neither_kiwipiepy_hook_survives():
    """A collect_all hook would repopulate what the excludes just removed."""
    for name in ("hook-kiwipiepy.py", "hook-kiwipiepy_model.py"):
        assert not (ROOT / "PyInstaller-Hooks" / name).exists()


def test_every_release_matrix_leg_installs_the_ko_engine():
    matrix = json.loads((ROOT / ".github" / "release-matrix.json").read_text(encoding="utf-8"))
    assert matrix
    for entry in matrix:
        assert "ko" in entry["install_target"], entry["platform"]


def test_the_release_preflight_builds_against_the_ko_extra():
    """The preflight venv must carry both optional engines the release bundles."""
    preflight = (ROOT / "scripts" / "release_preflight.sh").read_text(encoding="utf-8")
    assert '".[asr,zh,ko]"' in preflight

"""The vi pack in the frozen app: engine excluded from the graph, seeded + smoked in release, leg passes."""

from __future__ import annotations

import json
from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.languages.vi.pack import PACK
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'
ENGINE = ("underthesea", "underthesea_core", "joblib", "cloudpickle")


def test_the_installer_loads_the_manifest():
    assert load_pack("vi") == PACK


def test_the_engine_is_excluded_from_the_graph_not_pinned_into_it():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    _before, _, excludes = spec.partition("excludes=[")
    for name in ENGINE:
        assert f'        "{name}",\n' in excludes, name


def test_the_vi_tokenizer_and_pack_are_pinned_into_the_graph():
    from tests.unit.languages.test_zh_bundling import generated_language_hiddenimports

    generated = generated_language_hiddenimports()
    assert "anki_miner.languages.vi.tokenizer" in generated
    assert "anki_miner.languages.vi.pack" in generated


def test_the_ported_code_notice_ships_in_the_bundle():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "viet_text_tools"' in spec and "+ viet_text_tools_license_datas" in spec


def test_the_release_workflow_seeds_and_smokes_vietnamese():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "vi" in codes and codes.index("vi") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("vi" in group for group in requested)


def test_no_release_matrix_leg_installs_the_engine():
    matrix = json.loads((ROOT / ".github" / "release-matrix.json").read_text(encoding="utf-8"))
    for entry in matrix:
        assert "vi" not in entry["install_target"] and "languages" not in entry["install_target"], entry["platform"]


def test_the_vi_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("vi") == 0
    assert "BUNDLED_SMOKE_PASS: language vi" in capsys.readouterr().out

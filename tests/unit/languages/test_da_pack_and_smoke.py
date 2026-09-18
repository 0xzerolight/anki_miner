"""The da model pack, the CI pin, the release smoke leg and the CC BY-SA 4.0 model notice."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
DA_WHEEL_SHA256 = "8caeb4dc26f56de8abcf70399b202a0b2223d38664b1a59f1cb8db0000808bc9"
DA_WHEEL_URL = (
    "https://github.com/explosion/spacy-models/releases/download/"
    "da_core_news_sm-3.8.0/da_core_news_sm-3.8.0-py3-none-any.whl"
)
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'


def test_the_model_pack_requires_the_engine():
    pack = load_pack("da")
    assert pack is not None and pack.requires == ("_spacy",) and pack.approx_download_mb == 13
    (component,) = pack.components
    assert component.import_name == "da_core_news_sm" and component.universal is not None
    assert (component.universal.url, component.universal.sha256) == (DA_WHEEL_URL, DA_WHEEL_SHA256)
    assert component.sentinels == ("__init__.py", "da_core_news_sm-3.8.0/config.cfg", "da_core_news_sm-3.8.0/meta.json")


def test_ci_installs_the_same_model_bytes():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f'"da_core_news_sm @ {DA_WHEEL_URL}#sha256={DA_WHEEL_SHA256}"' in ci


def test_the_release_workflow_seeds_and_smokes_danish():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "da" in codes and codes.index("da") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("da" in group for group in requested)


def test_the_model_notice_ships_the_cc_by_sa_text_and_its_sources():
    notice = ROOT / "licenses" / "da_core_news_sm"
    assert (notice / "LICENSE").read_text(encoding="utf-8").startswith("Attribution-ShareAlike 4.0 International")
    readme = (notice / "README.md").read_text(encoding="utf-8")
    assert "UD Danish DDT" in readme and "DaNE" in readme and "languages/da/pack.py" in readme


def test_the_spec_excludes_the_model_and_wires_the_notice():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"da_core_news_sm",' in spec
    assert '"licenses", "da_core_news_sm"' in spec and "+ da_core_news_sm_license_datas" in spec


def test_the_da_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("da") == 0
    assert "BUNDLED_SMOKE_PASS: language da" in capsys.readouterr().out

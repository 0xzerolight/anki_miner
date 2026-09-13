"""The pt model pack, its CC BY-SA notice in the bundle, and the release smoke leg."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
PT_WHEEL_SHA256 = "c304fa04db3af73cd08a250feacf560506e15a2ec2469bd1b09f06847f6b455c"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("pt")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "pt_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == PT_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/pt_core_news_sm-3.8.0/pt_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == ("__init__.py", "pt_core_news_sm-3.8.0/config.cfg", "pt_core_news_sm-3.8.0/meta.json")


def test_the_cc_by_sa_notice_travels_with_the_bundle():
    notice = ROOT / "licenses" / "pt_core_news_sm"
    assert {"README.md", "LICENSE", "LICENSES_SOURCES"} <= {path.name for path in notice.iterdir()}
    assert (notice / "LICENSE").read_text(encoding="utf-8").startswith("Attribution-ShareAlike 4.0 International")
    readme = (notice / "README.md").read_text(encoding="utf-8")
    assert "CC BY-SA 4.0" in readme and "UD Portuguese Bosque" in readme and "WikiNER" in readme
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert 'os.path.join(project_root, "licenses", "pt_core_news_sm")' in spec
    assert "+ pt_core_news_sm_license_datas" in spec


def test_the_release_workflow_seeds_and_smokes_portuguese():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if "fetch_language_pack_seeds.py" in line and "run:" in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('lang_pack_seeds"', 1)[1].split()
        assert "pt" in codes and codes.index("pt") < codes.index("asr")  # en contract item 15: before asr
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("pt" in group for group in requested)


def test_the_pt_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("pt") == 0
    assert "BUNDLED_SMOKE_PASS: language pt" in capsys.readouterr().out

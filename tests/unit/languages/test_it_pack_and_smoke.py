"""The it model pack, the release smoke leg, and the in-process smoke."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
IT_WHEEL_SHA256 = "3f617bf9a8ae0418953cf1fbf014e10272684c4229e882a7fd748b637d0100bf"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("it")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "it_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == IT_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/it_core_news_sm-3.8.0/it_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == ("__init__.py", "it_core_news_sm-3.8.0/config.cfg", "it_core_news_sm-3.8.0/meta.json")


def test_the_release_workflow_seeds_and_smokes_italian():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if "fetch_language_pack_seeds.py" in line and "run:" in line]
    assert seed_lines and all(" it " in f"{line} " for line in seed_lines)
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("it" in group for group in requested)


def test_the_it_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("it") == 0
    assert "BUNDLED_SMOKE_PASS: language it" in capsys.readouterr().out

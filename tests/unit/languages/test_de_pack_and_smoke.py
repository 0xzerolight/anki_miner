"""The de model pack, the release smoke leg and the in-process smoke."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
DE_WHEEL_SHA256 = "fec69fec52b1780f2d269d5af7582a5e28028738bd3190532459aeb473bfa3e7"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("de")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "de_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == DE_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/de_core_news_sm-3.8.0/de_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == ("__init__.py", "de_core_news_sm-3.8.0/config.cfg", "de_core_news_sm-3.8.0/meta.json")


def test_the_release_workflow_seeds_and_smokes_german():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if "fetch_language_pack_seeds.py" in line and "run:" in line]
    assert seed_lines and all(" de" in line for line in seed_lines)
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("de" in group for group in requested)


def test_the_de_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("de") == 0
    assert "BUNDLED_SMOKE_PASS: language de" in capsys.readouterr().out

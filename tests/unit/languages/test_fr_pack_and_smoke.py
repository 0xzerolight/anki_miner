"""The fr model pack and the release smoke leg (the shared-seed copy in bundle_smoke.sh is en's)."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
FR_WHEEL_SHA256 = "7d6ad14cd5078e53147bfbf70fb9d433c6a3865b695fda2657140bbc59a27e29"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("fr")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "fr_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == FR_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/fr_core_news_sm-3.8.0/fr_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == ("__init__.py", "fr_core_news_sm-3.8.0/config.cfg", "fr_core_news_sm-3.8.0/meta.json")


def test_the_release_workflow_seeds_and_smokes_french():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if "fetch_language_pack_seeds.py" in line and "run:" in line]
    assert seed_lines and all("fr" in line.split() for line in seed_lines)
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("fr" in group for group in requested)


def test_the_fr_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("fr") == 0
    assert "BUNDLED_SMOKE_PASS: language fr" in capsys.readouterr().out

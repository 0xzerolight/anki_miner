"""The es model pack, the release smoke leg, and the in-process smoke."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
ES_WHEEL_SHA256 = "e451a83d6df79b87e9eed0cb553f03e99e36a3bab18a7b79f0dcfd1fdf875e12"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("es")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "es_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == ES_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/es_core_news_sm-3.8.0/es_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == ("__init__.py", "es_core_news_sm-3.8.0/config.cfg", "es_core_news_sm-3.8.0/meta.json")


def test_the_release_workflow_seeds_and_smokes_spanish():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if "fetch_language_pack_seeds.py" in line and "run:" in line]
    codes = [line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split() for line in seed_lines]
    assert codes and all("es" in group and group.index("es") < group.index("asr") for group in codes)
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("es" in group for group in requested)


def test_the_es_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("es") == 0
    assert "BUNDLED_SMOKE_PASS: language es" in capsys.readouterr().out

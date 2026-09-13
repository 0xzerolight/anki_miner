"""The ca model pack and the release smoke leg."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
CA_WHEEL_SHA256 = "e214211aa8da91c24ebdc453c2aa5f54fac09f44e01e65bcbdd3b0a5cb94d809"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("ca")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "ca_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == CA_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/ca_core_news_sm-3.8.0/"
        "ca_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == ("__init__.py", "ca_core_news_sm-3.8.0/config.cfg", "ca_core_news_sm-3.8.0/meta.json")
    assert pack.approx_download_mb == 20  # ceil(19,566,606 B / 1e6): the generator's rule (Stage S Task 20)


def test_the_release_workflow_seeds_and_smokes_catalan():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if "fetch_language_pack_seeds.py" in line and "run:" in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('lang_pack_seeds"', 1)[1].split()
        assert "ca" in codes and codes[-1] == "asr"  # en contract item 15: before asr
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("ca" in group for group in requested)


def test_the_ca_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("ca") == 0
    assert "BUNDLED_SMOKE_PASS: language ca" in capsys.readouterr().out

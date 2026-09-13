"""The nl model pack, the release smoke leg and the CC BY-SA 4.0 model notice."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
NL_WHEEL_SHA256 = "a76978477821f213ca76a46c686df1b1d41462905d4868bc53eac086adca8b7e"
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'


def test_the_model_pack_requires_the_engine():
    pack = load_pack("nl")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "nl_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == NL_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/"
        "nl_core_news_sm-3.8.0/nl_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == ("__init__.py", "nl_core_news_sm-3.8.0/config.cfg", "nl_core_news_sm-3.8.0/meta.json")


def test_the_release_workflow_seeds_and_smokes_dutch():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "nl" in codes and codes.index("nl") < codes.index("asr")  # contract item 15: before asr
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("nl" in group for group in requested)


def test_the_model_notice_ships_the_cc_by_sa_text_and_its_sources():
    notice = ROOT / "licenses" / "nl_core_news_sm"
    assert (notice / "LICENSE").read_text(encoding="utf-8").startswith("Attribution-ShareAlike 4.0 International")
    readme = (notice / "README.md").read_text(encoding="utf-8")
    assert "UD Dutch LassySmall" in readme and "UD Dutch Alpino" in readme and "languages/nl/pack.py" in readme


def test_the_spec_wires_the_model_notice():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "nl_core_news_sm"' in spec
    assert "+ nl_core_news_sm_license_datas" in spec


def test_the_nl_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("nl") == 0
    assert "BUNDLED_SMOKE_PASS: language nl" in capsys.readouterr().out

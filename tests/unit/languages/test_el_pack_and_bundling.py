"""The el model pack, its CI pin, the release smoke leg and the CC BY-NC-SA 3.0 model notice."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
EL_WHEEL_SHA256 = "18df59b7f099a20d6f7cc1f964a57408a4c1663b73b5110932c1ea24f66e3027"
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'


def test_the_model_pack_requires_the_engine():
    pack = load_pack("el")
    assert pack is not None and pack.requires == ("_spacy",) and pack.approx_download_mb == 13
    (component,) = pack.components
    assert component.import_name == "el_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == EL_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/"
        "el_core_news_sm-3.8.0/el_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.universal.member_prefix == "el_core_news_sm/"
    assert component.sentinels == ("__init__.py", "el_core_news_sm-3.8.0/config.cfg", "el_core_news_sm-3.8.0/meta.json")


def test_the_release_workflow_seeds_and_smokes_greek():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "el" in codes and codes.index("el") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("el" in group for group in requested)


def test_the_model_notice_ships_the_licence_and_its_sources():
    notice = ROOT / "licenses" / "el_core_news_sm"
    assert sorted(path.name for path in notice.iterdir()) == ["LICENSE", "LICENSES_SOURCES", "README.md"]
    assert "Attribution-NonCommercial-ShareAlike 3.0 Unported" in (notice / "LICENSE").read_text(encoding="utf-8")
    sources = (notice / "LICENSES_SOURCES").read_text(encoding="utf-8")
    assert "UD Greek GDT v2.8" in sources and "Greek NER Corpus" in sources
    readme = (notice / "README.md").read_text(encoding="utf-8")
    assert "CC BY-NC-SA 3.0" in readme and "anki_miner/languages/el/pack.py" in readme


def test_the_spec_wires_the_model_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "el_core_news_sm"' in spec
    assert "+ el_core_news_sm_license_datas" in spec
    assert '        "el_core_news_sm",\n' in spec


def test_the_el_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("el") == 0
    assert "BUNDLED_SMOKE_PASS: language el" in capsys.readouterr().out

"""The lt model pack, its CI pin, the release smoke leg and the CC BY-SA 4.0 model notice."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
LT_WHEEL_SHA256 = "c1f709112fd01771c10fbd2fe7e01d9d65712c8bccd9171065a29f91c9dc151e"
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'


def test_the_model_pack_requires_the_engine():
    pack = load_pack("lt")
    assert pack is not None and pack.requires == ("_spacy",) and pack.approx_download_mb == 14
    (component,) = pack.components
    assert component.import_name == "lt_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == LT_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/"
        "lt_core_news_sm-3.8.0/lt_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.universal.member_prefix == "lt_core_news_sm/"
    assert component.sentinels == ("__init__.py", "lt_core_news_sm-3.8.0/config.cfg", "lt_core_news_sm-3.8.0/meta.json")


def test_the_release_workflow_seeds_and_smokes_lithuanian():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "lt" in codes and codes.index("lt") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("lt" in group for group in requested)


def test_the_ci_matrix_installs_the_pinned_model():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"lt_core_news_sm-3.8.0-py3-none-any.whl#sha256={LT_WHEEL_SHA256}" in ci


def test_the_model_notice_ships_the_licence_and_its_sources():
    notice = ROOT / "licenses" / "lt_core_news_sm"
    assert sorted(path.name for path in notice.iterdir()) == ["LICENSE", "README.md", "SOURCES.txt"]
    assert "Attribution-ShareAlike 4.0 International" in (notice / "LICENSE").read_text(encoding="utf-8")
    sources = (notice / "SOURCES.txt").read_text(encoding="utf-8")
    assert "UD Lithuanian ALKSNIS v2.8" in sources and "TokenMill" in sources
    readme = (notice / "README.md").read_text(encoding="utf-8")
    assert "CC BY-SA 4.0" in readme and "anki_miner/languages/lt/pack.py" in readme


def test_the_spec_wires_the_model_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "lt_core_news_sm"' in spec
    assert "+ lt_core_news_sm_license_datas" in spec
    assert '        "lt_core_news_sm",\n' in spec


def test_the_lt_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("lt") == 0
    assert "BUNDLED_SMOKE_PASS: language lt" in capsys.readouterr().out

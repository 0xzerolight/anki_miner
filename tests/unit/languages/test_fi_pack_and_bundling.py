"""The fi model pack, its CI pin, the release smoke leg, the bundle exclusion and the CC BY-SA 4.0 model notice."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
FI_WHEEL_SHA256 = "5b9bd1496f500c1fac98f5e6a5d3913aa502f188c094ecea191dc6b8a81f418d"
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'
NOTICE = ROOT / "licenses" / "fi_core_news_sm"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("fi")
    assert pack is not None and pack.requires == ("_spacy",) and pack.approx_download_mb == 15
    (component,) = pack.components
    assert component.import_name == "fi_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == FI_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/"
        "fi_core_news_sm-3.8.0/fi_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == ("__init__.py", "fi_core_news_sm-3.8.0/config.cfg", "fi_core_news_sm-3.8.0/meta.json")


def test_ci_installs_the_same_wheel_bytes():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"fi_core_news_sm-3.8.0-py3-none-any.whl#sha256={FI_WHEEL_SHA256}" in ci


def test_the_release_workflow_seeds_and_smokes_finnish():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "fi" in codes and codes.index("fi") < codes.index("asr")  # contract item 15: before asr
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("fi" in group for group in requested)


def test_the_notice_is_the_wheels_cc_by_sa_text_and_names_both_sources():
    import fi_core_news_sm

    shipped = Path(fi_core_news_sm.__file__).parent / "fi_core_news_sm-3.8.0" / "LICENSE"
    assert (NOTICE / "LICENSE").read_bytes() == shipped.read_bytes()
    assert (NOTICE / "LICENSE").read_text(encoding="utf-8").startswith("Attribution-ShareAlike 4.0 International")
    readme = (NOTICE / "README.md").read_text(encoding="utf-8")
    assert "UD Finnish TDT v2.8" in readme and "TurkuONE" in readme and "languages/fi/pack.py" in readme
    assert sorted(path.name for path in NOTICE.iterdir()) == ["LICENSE", "README.md"]


def test_the_spec_ships_the_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "fi_core_news_sm"' in spec and "+ fi_core_news_sm_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    assert '"fi_core_news_sm",' in excludes


def test_the_fi_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("fi") == 0
    assert "BUNDLED_SMOKE_PASS: language fi" in capsys.readouterr().out

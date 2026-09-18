"""The hr model pack, the release seed and smoke rows, and the CC BY-SA 4.0 model notice."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
HR_WHEEL_SHA256 = "393ad455bba13536e6fb257bce2b5ef0253f41c1fb5249d8c16dde6cf116d196"
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'
NOTICE = ROOT / "licenses" / "hr_core_news_sm"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("hr")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "hr_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == HR_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/"
        "hr_core_news_sm-3.8.0/hr_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == (
        "__init__.py",
        "hr_core_news_sm-3.8.0/config.cfg",
        "hr_core_news_sm-3.8.0/meta.json",
    )
    assert pack.approx_download_mb == 14  # ceil(13,187,227 B / 1e6): the generator's rule


def test_ci_installs_the_pinned_model():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"hr_core_news_sm-3.8.0-py3-none-any.whl#sha256={HR_WHEEL_SHA256}" in workflow


def test_the_release_workflow_seeds_and_smokes_croatian():
    """Both release.yml rows: the pack seed codes and BUNDLE_SMOKE_LANGS (a helper that edits one drops the other)."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "hr" in codes and codes.index("hr") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("hr" in group for group in requested)


def test_the_model_notice_is_the_wheels_cc_by_sa_text_with_its_source():
    import hr_core_news_sm

    shipped = Path(hr_core_news_sm.__file__).parent / "hr_core_news_sm-3.8.0" / "LICENSE"
    assert (NOTICE / "LICENSE").read_bytes() == shipped.read_bytes()
    assert (NOTICE / "LICENSE").read_text(encoding="utf-8").startswith("Attribution-ShareAlike 4.0 International")
    readme = (NOTICE / "README.md").read_text(encoding="utf-8")
    assert "hr500k" in readme and "CC BY-SA 4.0" in readme and "languages/hr/pack.py" in readme
    assert sorted(path.name for path in NOTICE.iterdir()) == ["LICENSE", "README.md"]


def test_the_spec_ships_the_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "hr_core_news_sm"' in spec
    assert "+ hr_core_news_sm_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    assert '"hr_core_news_sm",' in excludes

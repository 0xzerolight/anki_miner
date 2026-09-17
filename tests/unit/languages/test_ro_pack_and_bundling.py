"""The ro model pack, the release seed and smoke rows, and the CC BY-SA 4.0 model notice."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
RO_WHEEL_SHA256 = "3cebfba2437131efe95345adcc7ea2131e4463915652df9099c577e618460cd9"
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'
NOTICE = ROOT / "licenses" / "ro_core_news_sm"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("ro")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "ro_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == RO_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/"
        "ro_core_news_sm-3.8.0/ro_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == (
        "__init__.py",
        "ro_core_news_sm-3.8.0/config.cfg",
        "ro_core_news_sm-3.8.0/meta.json",
    )
    assert pack.approx_download_mb == 13  # ceil(12,910,222 B / 1e6): the generator's rule


def test_the_release_workflow_seeds_and_smokes_romanian():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "ro" in codes and codes.index("ro") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("ro" in group for group in requested)


def test_the_model_notice_is_the_wheels_cc_by_sa_text_with_its_sources():
    import ro_core_news_sm

    shipped = Path(ro_core_news_sm.__file__).parent / "ro_core_news_sm-3.8.0" / "LICENSE"
    assert (NOTICE / "LICENSE").read_bytes() == shipped.read_bytes()
    assert (NOTICE / "LICENSE").read_text(encoding="utf-8").startswith("Attribution-ShareAlike 4.0 International")
    readme = (NOTICE / "README.md").read_text(encoding="utf-8")
    assert "UD Romanian RRT" in readme and "RONEC" in readme and "languages/ro/pack.py" in readme
    assert sorted(path.name for path in NOTICE.iterdir()) == ["LICENSE", "README.md"]


def test_the_spec_ships_the_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "ro_core_news_sm"' in spec
    assert "+ ro_core_news_sm_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    assert '"ro_core_news_sm",' in excludes

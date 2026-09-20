"""The sl model pack, the release seed and smoke rows, and the CC BY-SA 4.0 model notice."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
SL_WHEEL_SHA256 = "d6d11f38c917d59b0fe52a15da09963bdc8011d2be288f75371e734065b6911c"
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'
NOTICE = ROOT / "licenses" / "sl_core_news_sm"
WHEEL = (
    "https://github.com/explosion/spacy-models/releases/download/"
    "sl_core_news_sm-3.8.0/sl_core_news_sm-3.8.0-py3-none-any.whl"
)


def test_the_model_pack_requires_the_engine():
    pack = load_pack("sl")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "sl_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == SL_WHEEL_SHA256
    assert component.universal.url == WHEEL
    assert component.sentinels == (
        "__init__.py",
        "sl_core_news_sm-3.8.0/config.cfg",
        "sl_core_news_sm-3.8.0/meta.json",
    )
    assert pack.approx_download_mb == 14  # ceil(13,643,819 B / 1e6): the generator's rule


def test_ci_installs_the_pinned_model():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"sl_core_news_sm-3.8.0-py3-none-any.whl#sha256={SL_WHEEL_SHA256}" in workflow


def test_the_release_workflow_seeds_and_smokes_slovenian():
    """Both release.yml rows: the seed codes and BUNDLE_SMOKE_LANGS (a helper that edits one drops the other)."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "sl" in codes and codes.index("sl") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("sl" in group for group in requested)


def test_the_model_notice_is_the_wheels_cc_by_sa_text_with_its_sources():
    import sl_core_news_sm

    shipped = Path(sl_core_news_sm.__file__).parent / "sl_core_news_sm-3.8.0" / "LICENSE"
    assert (NOTICE / "LICENSE").read_bytes() == shipped.read_bytes()
    assert (NOTICE / "LICENSE").read_text(encoding="utf-8").startswith("Attribution-ShareAlike 4.0 International")
    readme = (NOTICE / "README.md").read_text(encoding="utf-8")
    assert "SSJ" in readme and "SUK 1.0" in readme and "CC BY-SA 4.0" in readme
    assert "languages/sl/pack.py" in readme
    assert sorted(path.name for path in NOTICE.iterdir()) == ["LICENSE", "README.md"]


def test_the_spec_ships_the_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "sl_core_news_sm"' in spec
    assert "+ sl_core_news_sm_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    assert '"sl_core_news_sm",' in excludes


def test_the_sl_extra_installs_spacy():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '\nsl = [\n    "spacy>=3.8,<3.8.15",\n]\n' in pyproject
    assert '"anki-miner[sl]"' in pyproject

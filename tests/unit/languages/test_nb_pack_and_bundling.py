"""The nb model pack, the CI pin, the release smoke leg and the MIT model notice."""

from __future__ import annotations

import hashlib
from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
LICENSE_DIR = ROOT / "licenses" / "nb_core_news_sm"
NB_WHEEL_SHA256 = "086f2cfcc1568b2ee49a2ab297f7910f2645ae6892f2d5d15d8b077f5d0bd773"
MIT_SHA256 = "3933c176979b68bc6d0bcc902c7d6c130f1d127f476f17ba5cdba8d99cfd0012"
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'


def test_the_model_pack_requires_the_engine():
    pack = load_pack("nb")
    assert pack is not None and pack.requires == ("_spacy",) and pack.approx_download_mb == 13
    (component,) = pack.components
    assert component.import_name == "nb_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == NB_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/"
        "nb_core_news_sm-3.8.0/nb_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == ("__init__.py", "nb_core_news_sm-3.8.0/config.cfg", "nb_core_news_sm-3.8.0/meta.json")


def test_the_release_workflow_seeds_and_smokes_norwegian():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "nb" in codes and codes.index("nb") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("nb" in group for group in requested)


def test_the_notice_carries_the_mit_text_and_the_source_pointer():
    text = (LICENSE_DIR / "LICENSE").read_bytes()
    assert hashlib.sha256(text).hexdigest() == MIT_SHA256
    sources = (LICENSE_DIR / "SOURCES.txt").read_text(encoding="utf-8")
    assert "https://github.com/explosion/spacy-models/releases/tag/nb_core_news_sm-3.8.0" in sources
    assert "UD Norwegian Bokmaal" in sources and "NorNE" in sources and "anki_miner/languages/nb/pack.py" in sources
    assert "LICENSES_SOURCES" in sources
    assert sorted(path.name for path in LICENSE_DIR.iterdir()) == ["LICENSE", "README.md", "SOURCES.txt"]
    assert "MIT" in (LICENSE_DIR / "README.md").read_text(encoding="utf-8")


def test_the_text_is_the_one_inside_the_model_wheel():
    import nb_core_news_sm

    shipped = Path(nb_core_news_sm.__file__).parent / "nb_core_news_sm-3.8.0" / "LICENSE"
    assert shipped.read_bytes() == (LICENSE_DIR / "LICENSE").read_bytes()


def test_the_spec_ships_the_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "nb_core_news_sm"' in spec
    assert "+ nb_model_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    assert '"nb_core_news_sm",' in excludes


def test_the_nb_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("nb") == 0
    assert "BUNDLED_SMOKE_PASS: language nb" in capsys.readouterr().out

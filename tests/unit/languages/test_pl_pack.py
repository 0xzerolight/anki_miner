"""The pl model pack, its CI pin and the release seed/smoke lines."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
PL_WHEEL_SHA256 = "9b536db854b0cdb9132a74f68b392b8112ab6030f971bb03282462362a940dbb"
PL_WHEEL_URL = (
    "https://github.com/explosion/spacy-models/releases/download/"
    "pl_core_news_sm-3.8.0/pl_core_news_sm-3.8.0-py3-none-any.whl"
)
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'


def test_the_model_pack_requires_the_engine():
    pack = load_pack("pl")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "pl_core_news_sm" and component.universal is not None
    assert (component.universal.url, component.universal.sha256) == (PL_WHEEL_URL, PL_WHEEL_SHA256)
    assert component.sentinels == ("__init__.py", "pl_core_news_sm-3.8.0/config.cfg", "pl_core_news_sm-3.8.0/meta.json")
    assert pack.approx_download_mb == 21  # ceil(20,213,213 B / 1e6)


def test_ci_installs_the_same_wheel_bytes():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f'"pl_core_news_sm @ {PL_WHEEL_URL}#sha256={PL_WHEEL_SHA256}"' in ci


def test_the_release_workflow_seeds_and_smokes_polish():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "pl" in codes and codes.index("pl") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("pl" in group for group in requested)

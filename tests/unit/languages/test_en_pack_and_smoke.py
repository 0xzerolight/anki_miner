"""The en model pack, the release smoke leg, and the shared-seed copy in bundle_smoke.sh."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from anki_miner.gui import app as app_module
from anki_miner.services.language_pack_installer import load_pack
from tests.unit.languages.test_ko_smoke_leg import _fake_dist, _run_smoke

ROOT = Path(__file__).resolve().parents[3]
EN_WHEEL_SHA256 = "1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("en")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "en_core_web_sm" and component.universal is not None
    assert component.universal.sha256 == EN_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == ("__init__.py", "en_core_web_sm-3.8.0/config.cfg", "en_core_web_sm-3.8.0/meta.json")


def test_the_release_workflow_seeds_and_smokes_english():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if "fetch_language_pack_seeds.py" in line and "run:" in line]
    assert seed_lines and all(" en" in line for line in seed_lines)
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("en" in group for group in requested)


def test_the_smoke_script_names_no_shared_pack_code():
    assert "_spacy" not in (ROOT / "scripts" / "bundle_smoke.sh").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_the_smoke_copies_the_shared_engine_seed_beside_the_language(tmp_path):
    """Runs bundle_smoke.sh (the test_ko_smoke_leg harness): the en leg passes only with _spacy in its home."""
    dist = _fake_dist(tmp_path)
    app = dist / "AnkiMiner"
    app.write_text(
        app.read_text(encoding="utf-8").replace(
            "  zh)\n",
            "  en)\n"
            '    test -f "$ANKI_MINER_HOME/language_packs/en/en_core_web_sm/__init__.py"\n'
            '    test -f "$ANKI_MINER_HOME/language_packs/_spacy/spacy/__init__.py"\n'
            "    printf '%s\\n' en >> \"$SEED_RECORD\"\n"
            "    echo BUNDLED_SMOKE_PASS ;;\n"
            "  zh)\n",
        ),
        encoding="utf-8",
    )
    seeds = tmp_path / "fetched"
    for relative in ("en/en_core_web_sm/__init__.py", "_spacy/spacy/__init__.py"):
        (seeds / relative).parent.mkdir(parents=True, exist_ok=True)
        (seeds / relative).write_bytes(b"")
    record = tmp_path / "seeded.txt"

    result = _run_smoke(tmp_path, dist, record, seeds, langs="en")

    assert "PASS language-en" in result.stdout, result.stdout[-2000:] + result.stderr[-2000:]
    assert record.read_text(encoding="utf-8").split() == ["en"]


def test_the_en_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("en") == 0
    assert "BUNDLED_SMOKE_PASS: language en" in capsys.readouterr().out

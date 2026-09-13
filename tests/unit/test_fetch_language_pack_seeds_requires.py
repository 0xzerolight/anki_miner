"""S18: the seeder's manifest names what a pack requires."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from anki_miner.languages.pack_spec import LanguagePack

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_language_pack_seeds.py"
_spec = importlib.util.spec_from_file_location("fetch_language_pack_seeds_requires", _SCRIPT)
assert _spec is not None and _spec.loader is not None
seeds = importlib.util.module_from_spec(_spec)
sys.modules["fetch_language_pack_seeds_requires"] = seeds
_spec.loader.exec_module(seeds)


def test_print_manifest_lists_requirements(tmp_path, monkeypatch, capsys):
    pack = LanguagePack(code="zz", approx_download_mb=12, requires=("_spacy",))
    monkeypatch.setattr(seeds, "load_pack", lambda code: pack if code == "zz" else None)
    monkeypatch.setattr(seeds, "pack_supported", lambda code: True)

    assert seeds.main([str(tmp_path), "zz", "--print-manifest"]) == 0

    (entry,) = json.loads(capsys.readouterr().out)["packs"]
    assert entry["requires"] == ["_spacy"]

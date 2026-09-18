"""The hu model pack (a HuggingFace wheel), the release smoke leg, the licence notices and the factory import (D12).

``pack.py``'s generated header says "regenerate it", but ``scripts/pin_language_pack.py model`` resolves GitHub
release assets only: this model is a HuggingFace asset, so the URL and sha256 below were taken from
``https://huggingface.co/api/models/huspacy/hu_core_news_md/tree/v3.8.0`` (the wheel's ``size`` and its LFS ``oid``)
and rendered with ``pin_language_pack.render_manifest``. The recurring verification is not a script but
``.github/workflows/ci.yml``: its ``test`` job downloads this wheel and runs ``sha256sum -c`` against this same
pin on every run, so moved bytes fail CI before pytest.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.gui.widgets.panels.mining_language_settings_panel import pack_already_importable
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
HU_WHEEL_URL = "https://huggingface.co/huspacy/hu_core_news_md/resolve/v3.8.0/hu_core_news_md-any-py3-none-any.whl"
HU_WHEEL_SHA256 = "0fd89c6ccf0efe1d7591910065c3bec4eadb1e25313d6ceea551150832b0f861"
HU_WHEEL_BYTES = 127_018_056
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'
CC_BY_SA_4_SHA256 = "3b2890eacd851373001c4a14623458e3adaf1b1967939aa9c38a318e28d61c00"
CC_BY_NC_SA_3_SHA256 = "8812f83442fd0eca14eb0208988e190fdcbfebec58fa5459d3218edfdfdc5a32"
PIPELINE = ["tok2vec", "tagger", "morphologizer", "lookup_lemmatizer", "trainable_lemmatizer", "parser"]


def test_the_model_pack_is_the_huggingface_wheel_behind_the_engine():
    pack = load_pack("hu")
    assert pack is not None and pack.requires == ("_spacy",) and pack.approx_download_mb == 128
    (component,) = pack.components
    assert component.import_name == "hu_core_news_md" and component.universal is not None
    assert (component.universal.url, component.universal.sha256) == (HU_WHEEL_URL, HU_WHEEL_SHA256)
    assert component.universal.kind == "wheel" and component.universal.member_prefix == "hu_core_news_md/"
    assert component.sentinels == ("__init__.py", "hu_core_news_md-3.8.0/config.cfg", "hu_core_news_md-3.8.0/meta.json")


def test_the_resolved_fixture_carries_the_measured_size_and_the_generator_rounding():
    fixture = ROOT / "tests" / "fixtures" / "language_packs" / "hu.resolved.json"
    resolved = json.loads(fixture.read_text(encoding="utf-8"))
    (component,) = resolved["components"]
    assert component["universal"]["size"] == HU_WHEEL_BYTES
    assert resolved["approx_download_mb"] == -(-HU_WHEEL_BYTES // 1_000_000)


def test_an_importable_model_hides_the_download_row():
    """The model component alone: ``requires=("_spacy",)`` drags in the engine pack's 30 packages, and a dev venv
    that installed spaCy from PyPI may lack an optional one of them (colorama), which says nothing about hu."""
    pack = load_pack("hu")
    assert pack is not None
    assert pack_already_importable(dataclasses.replace(pack, requires=()))


def test_the_release_workflow_seeds_and_smokes_hungarian():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "hu" in codes and codes.index("hu") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("hu" in group for group in requested)


def test_the_notice_ships_both_licence_texts_and_every_training_source():
    notice = ROOT / "licenses" / "hu_core_news_md"
    assert sorted(path.name for path in notice.iterdir()) == ["LICENSE", "LICENSE.CC-BY-NC-SA-3.0", "README.md"]
    assert hashlib.sha256((notice / "LICENSE").read_bytes()).hexdigest() == CC_BY_SA_4_SHA256
    assert hashlib.sha256((notice / "LICENSE.CC-BY-NC-SA-3.0").read_bytes()).hexdigest() == CC_BY_NC_SA_3_SHA256
    readme = (notice / "README.md").read_text(encoding="utf-8")
    import hu_core_news_md

    meta = json.loads((Path(hu_core_news_md.__file__).parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["license"] == "cc-by-sa-4.0"
    assert all(source["name"] in readme and source["url"] in readme for source in meta["sources"])
    assert {source["license"] for source in meta["sources"]} == {"CC-BY-NC-SA-3.0", "CC BY-SA 4.0", "CC-BY-SA-4.0"}
    assert "CC BY-NC-SA 3.0" in readme and "anki_miner/languages/hu/pack.py" in readme


def test_the_spec_ships_the_notice_and_excludes_the_model():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "hu_core_news_md"' in spec and "+ hu_core_news_md_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    assert '"hu_core_news_md",' in excludes


def test_the_hu_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("hu") == 0
    assert "BUNDLED_SMOKE_PASS: language hu" in capsys.readouterr().out


_REGISTRATION_PROBE = """
import importlib.metadata, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from spacy import registry
from spacy.util import load_model_from_path

wanted = {"hu.lookup_lemmatizer", "trainable_lemmatizer_v2"}
out = {"before": sorted(wanted & set(registry.factories.get_all()))}
try:
    # A Path, never a str: load_model_from_path calls model_path.exists() before anything else.
    load_model_from_path(Path(sys.argv[2]), exclude=["ner", "senter"])
    out["path_only"] = "loaded"
except ValueError as exc:
    out["path_only"] = str(exc)[:6]
try:
    importlib.metadata.distribution("zz_hu_pack_md")
    out["dist_info"] = True
except importlib.metadata.PackageNotFoundError:
    out["dist_info"] = False
from anki_miner.languages._spaced.tokenizer import load_spacy_model
nlp = load_spacy_model("zz_hu_pack_md", keep_parser=True)
out["after"] = sorted(wanted & set(registry.factories.get_all()))
out["pipeline"] = nlp.pipe_names
out["lemmas"] = [token.lemma_ for token in nlp("A házakban olvastam.")]
print(json.dumps(out))
"""


def test_the_package_import_registers_the_factories_a_path_load_never_sees(tmp_path):
    """E.9/D12 in a fresh interpreter: a dist-info-less copy of the package on a temp sys.path root (the pack layout).

    The data directory is symlinked, not copied (146 MB); the four modules and meta.json are copied.
    """
    import hu_core_news_md

    installed = Path(hu_core_news_md.__file__).parent
    package = tmp_path / "zz_hu_pack_md"
    package.mkdir()
    for name in (
        "__init__.py",
        "edit_tree_lemmatizer.py",
        "lemma_postprocessing.py",
        "lookup_lemmatizer.py",
        "meta.json",
    ):
        shutil.copy2(installed / name, package / name)
    data = package / "hu_core_news_md-3.8.0"
    data.symlink_to(installed / "hu_core_news_md-3.8.0", target_is_directory=True)
    result = subprocess.run(
        [sys.executable, "-c", _REGISTRATION_PROBE, str(tmp_path), str(data)],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["before"] == [] and out["path_only"] == "[E002]" and out["dist_info"] is False
    assert out["after"] == ["hu.lookup_lemmatizer", "trainable_lemmatizer_v2"]
    assert out["pipeline"] == PIPELINE
    assert out["lemmas"][1:3] == ["ház", "olvas"]


_LEGACY_PROBE = """
import importlib.metadata, json


def _without_spacy_legacy(**params):
    # Built from distributions(): a bare entry_points() is the dict-like SelectableGroups on 3.11.
    kept = importlib.metadata.EntryPoints(
        ep
        for dist in importlib.metadata.distributions()
        for ep in dist.entry_points
        if not ep.value.startswith("spacy_legacy.")
    )
    return kept.select(**params) if params else kept


# Before spaCy imports: catalogue snapshots the entry points once, at its own import.
importlib.metadata.entry_points = _without_spacy_legacy
from spacy import registry

out = {"bare": registry.has("architectures", "spacy.Tagger.v1")}
from anki_miner.languages._spaced.tokenizer import load_spacy_model
nlp = load_spacy_model("hu_core_news_md", keep_parser=True)
out["pipeline"] = nlp.pipe_names
out["lemmas"] = [token.lemma_ for token in nlp("A házakban olvastam.")]
print(json.dumps(out))
"""


def test_the_legacy_tagger_loads_without_spacy_legacys_entry_points():
    """A pack extracts ``spacy_legacy`` without its dist-info, so catalogue sees none of its entry points.

    hu's tagger is ``spacy.Tagger.v1``, which spaCy only finds through spacy-legacy's
    ``spacy_architectures`` entry points; every frozen build died on it (E893).
    """
    result = subprocess.run([sys.executable, "-c", _LEGACY_PROBE], capture_output=True, text=True, check=True, cwd=ROOT)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["bare"] is False
    assert out["pipeline"] == PIPELINE
    assert out["lemmas"][1:3] == ["ház", "olvas"]

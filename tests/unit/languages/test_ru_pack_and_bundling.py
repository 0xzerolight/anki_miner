"""The ru pack (model + pymorphy3 + dawg2-python + dicts-ru, the dicts' .dist-info as a root member),
its frozen-layout proofs, the release smoke leg and the licence notices (plan D4-D6, D14)."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from anki_miner.gui import app as app_module
from anki_miner.gui.widgets.panels.mining_language_settings_panel import pack_already_importable
from anki_miner.services.language_pack_installer import load_pack
from anki_miner.services.pack_installer import component_complete

ROOT = Path(__file__).resolve().parents[3]
DICTS_VERSION = "2.4.417150.4580142"
DIST_INFO = f"pymorphy3_dicts_ru-{DICTS_VERSION}.dist-info"
MODEL_URL = "https://github.com/explosion/spacy-models/releases/download/ru_core_news_sm-3.8.0/ru_core_news_sm-3.8.0-py3-none-any.whl"
MODEL_SHA256 = "69978d47b43e2c4f329bebdb155e8e9d3861bba1a58ba25551419dae7d7e07fc"
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'


def _site_dir(module: str) -> Path:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).parent


def test_the_pack_is_the_model_and_its_morphology():
    pack = load_pack("ru")
    assert pack is not None and pack.requires == ("_spacy",) and pack.approx_download_mb == 24
    names = [comp.import_name for comp in pack.components]
    assert names == ["ru_core_news_sm", "pymorphy3", "dawg_python", "pymorphy3_dicts_ru"]
    model, *companions = pack.components
    assert model.universal is not None and (model.universal.url, model.universal.sha256) == (MODEL_URL, MODEL_SHA256)
    for comp in companions:
        assert comp.universal is not None and comp.universal.url.startswith("https://files.pythonhosted.org/")
        assert comp.universal.member_prefix == f"{comp.import_name}/"
    roots = {comp.import_name: comp.universal.root_members for comp in companions if comp.universal}
    assert roots == {"pymorphy3": (), "dawg_python": (), "pymorphy3_dicts_ru": (f"{DIST_INFO}/",)}


def test_the_resolved_fixture_sizes_fit_the_advertised_download():
    resolved = json.loads(
        (ROOT / "tests" / "fixtures" / "language_packs" / "ru.resolved.json").read_text(encoding="utf-8")
    )
    total = sum(comp["universal"]["size"] for comp in resolved["components"])
    assert total == 23_764_896 and resolved["approx_download_mb"] == -(-total // 1_000_000)


def _pack_root(tmp_path: Path, *, with_dist_info: bool) -> Path:
    """A pack root built from the installed packages: the three package dirs (data symlinked), and the dicts'
    .dist-info exactly as the root member lands it."""
    root = tmp_path / ("with" if with_dist_info else "without")
    root.mkdir()
    for module in ("pymorphy3", "dawg_python"):
        shutil.copytree(_site_dir(module), root / module, ignore=shutil.ignore_patterns("__pycache__"))
    dicts = _site_dir("pymorphy3_dicts_ru")
    (root / "pymorphy3_dicts_ru").mkdir()
    for name in ("__init__.py", "version.py"):
        shutil.copy2(dicts / name, root / "pymorphy3_dicts_ru" / name)
    (root / "pymorphy3_dicts_ru" / "data").symlink_to(dicts / "data", target_is_directory=True)
    if with_dist_info:
        shutil.copytree(dicts.parent / DIST_INFO, root / DIST_INFO)
    return root


_ISOLATED_PROBE = """
import sys
sys.path.append(sys.argv[1])
import pymorphy3
analyzer = pymorphy3.MorphAnalyzer(lang="ru")
print(analyzer.parse("читала")[0].normal_form, pymorphy3.__file__.startswith(sys.argv[1]))
"""


@pytest.mark.parametrize("with_dist_info", [True, False])
def test_pymorphy3_finds_its_dictionaries_from_a_pack_root_only_through_the_dist_info(tmp_path, with_dist_info):
    """``-I -S``: no site-packages, only the pack root, as in the frozen app. The closure imports nothing else."""
    root = _pack_root(tmp_path, with_dist_info=with_dist_info)
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", _ISOLATED_PROBE, str(root)], capture_output=True, text=True, check=False
    )
    if with_dist_info:
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == ["читать", "True"]
    else:
        assert result.returncode != 0 and "Can't find a dictionary for language 'ru'" in result.stderr


def test_the_installer_counts_the_dicts_incomplete_without_the_dist_info(tmp_path):
    pack = load_pack("ru")
    assert pack is not None
    dicts = next(comp for comp in pack.components if comp.import_name == "pymorphy3_dicts_ru")
    assert component_complete(_pack_root(tmp_path, with_dist_info=True), dicts)
    assert not component_complete(_pack_root(tmp_path, with_dist_info=False), dicts)


_MODEL_PROBE = """
import importlib.metadata, json, sys
sys.path.insert(0, sys.argv[1])
try:
    importlib.metadata.distribution("zz_ru_pack_sm")
    dist_info = True
except importlib.metadata.PackageNotFoundError:
    dist_info = False
from anki_miner.languages._spaced.tokenizer import load_spacy_model
nlp = load_spacy_model("zz_ru_pack_sm", keep_parser=False)
sentence = "Студентка читала книги."
print(json.dumps({"dist_info": dist_info, "pipeline": nlp.pipe_names, "lemmas": [t.lemma_ for t in nlp(sentence)]}))
"""


def test_a_dist_info_less_model_package_loads_through_the_adapter(tmp_path):
    """Spec B.8: the pack drops the model's .dist-info; the package's own load() reads meta.json beside it."""
    installed = _site_dir("ru_core_news_sm")
    package = tmp_path / "zz_ru_pack_sm"
    package.mkdir()
    for name in ("__init__.py", "meta.json"):
        shutil.copy2(installed / name, package / name)
    (package / "ru_core_news_sm-3.8.0").symlink_to(installed / "ru_core_news_sm-3.8.0", target_is_directory=True)
    result = subprocess.run(
        [sys.executable, "-c", _MODEL_PROBE, str(tmp_path)], capture_output=True, text=True, check=True, cwd=ROOT
    )
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["dist_info"] is False
    assert out["pipeline"] == ["tok2vec", "morphologizer", "attribute_ruler", "lemmatizer"]
    assert out["lemmas"][:3] == [
        "студентка",
        "читать",
        "книга",
    ]


def test_an_importable_pack_hides_the_download_row():
    pack = load_pack("ru")
    assert pack is not None
    assert pack_already_importable(dataclasses.replace(pack, requires=()))


def test_the_release_workflow_seeds_and_smokes_russian():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_lines = [line for line in workflow.splitlines() if SEED_ANCHOR in line]
    assert seed_lines
    for line in seed_lines:
        codes = line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
        assert "ru" in codes and codes.index("ru") < codes.index("asr")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("ru" in group for group in requested)


def test_the_notices_ship_and_the_engine_is_excluded():
    model_notice = ROOT / "licenses" / "ru_core_news_sm"
    assert sorted(path.name for path in model_notice.iterdir()) == ["LICENSE", "README.md", "SOURCES.txt"]
    shipped = _site_dir("ru_core_news_sm") / "ru_core_news_sm-3.8.0" / "LICENSE"
    assert (model_notice / "LICENSE").read_bytes() == shipped.read_bytes()
    assert "MIT" in (model_notice / "README.md").read_text(encoding="utf-8")
    assert "Nerus" in (model_notice / "SOURCES.txt").read_text(encoding="utf-8")
    dicts_notice = ROOT / "licenses" / "pymorphy3_dicts_ru"
    assert sorted(path.name for path in dicts_notice.iterdir()) == ["README.md", "SOURCES.txt"]
    readme = (dicts_notice / "README.md").read_text(encoding="utf-8")
    assert "OpenCorpora" in readme and "ShareAlike 3.0" in readme and "anki_miner/languages/ru/pack.py" in readme
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    for name in ("ru_core_news_sm", "pymorphy3_dicts_ru"):
        assert f'"licenses", "{name}"' in spec and f"+ {name}_license_datas" in spec
    _head, _, excludes = spec.partition("excludes=[")
    for name in ("ru_core_news_sm", "pymorphy3", "dawg_python", "pymorphy3_dicts_ru"):
        assert f'"{name}",' in excludes


def test_the_ru_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("ru") == 0
    assert "BUNDLED_SMOKE_PASS: language ru" in capsys.readouterr().out

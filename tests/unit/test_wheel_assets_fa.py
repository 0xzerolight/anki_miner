"""The Persian sources' licence notices ship everywhere their material does.

Three licences travel with a Persian install: hazm (MIT) because
``anki_miner/languages/fa/_hazm/`` is a port of its code and the language pack
is its data, shekar (MIT) because 108 rows of ``colloquial.tsv`` come from it,
and CC BY-SA 4.0 because ``compound_verbs.tsv`` is derived from Wiktionary (as
are the wty dictionaries the app downloads).

Unlike the twenty-three spaCy-model notices, these have to reach BOTH
distributions: the ported code and the two data tables ship inside
``anki_miner/`` in the wheel AND in the frozen bundle. ``pyproject``'s
package-data is wheel-only by its own comment, so the bundle needs its own
``datas`` entry for the tables too -- without it a frozen build finds no
compound-verb table and every Persian compound silently mines as two tokens.
"""

from __future__ import annotations

import ast
import importlib.util
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTICES = (
    "licenses/hazm/LICENSE",
    "licenses/shekar/LICENSE",
    "licenses/wiktionary/LICENSE.CC-BY-SA-4.0",
)
READMES = (
    "licenses/hazm/README.md",
    "licenses/shekar/README.md",
    "licenses/wiktionary/README.md",
)
LICENSE_DIRS = ("hazm", "shekar", "wiktionary")


def _literal(name: str, path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in {path}")


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _spec() -> str:
    return (ROOT / "anki_miner.spec").read_text(encoding="utf-8")


@pytest.mark.parametrize("notice", NOTICES)
def test_the_notice_exists_and_carries_a_copyright(notice):
    text = (ROOT / notice).read_text(encoding="utf-8")
    assert "Copyright" in text or "Creative Commons" in text


@pytest.mark.parametrize("notice", [*NOTICES, *READMES])
def test_the_notice_is_promised_to_the_wheel(notice):
    assert notice in _pyproject()["project"]["license-files"]


@pytest.mark.parametrize("notice", NOTICES)
def test_the_licence_text_is_checked_into_the_wheel(notice):
    assert notice in _literal("REQUIRED_WHEEL_LICENSES", ROOT / "scripts" / "check_wheel_assets.py")


@pytest.mark.parametrize("name", LICENSE_DIRS)
def test_the_notice_is_collected_into_the_bundle(name):
    # The kiwipiepy mould (anki_miner.spec:174-182): each notice gets its own
    # conditional block and its own line in the datas sum.
    spec = _spec()
    assert f'{name}_license_dir = os.path.join(project_root, "licenses", "{name}")' in spec
    assert f"+ {name}_license_datas" in spec


def test_the_persian_data_tables_are_collected_into_the_bundle():
    assert 'os.path.join("anki_miner", "languages", "fa", "data")' in _spec()


def test_the_persian_data_tables_are_shipped_in_the_wheel():
    package_data = _pyproject()["tool"]["setuptools"]["package-data"]
    assert "*.tsv" in package_data["anki_miner.languages.fa.data"]


@pytest.mark.parametrize("table", ["compound_verbs.tsv", "colloquial.tsv"])
def test_the_persian_data_tables_are_named_in_the_wheel_gate(table):
    # REQUIRED_ASSETS is the only gate that survives a file deleted from disk
    # AND from the wheel in the same change: the generic disk-vs-wheel
    # comparison sees two equal sets and stays green, and RESOURCE_DIRS does
    # not cover languages/fa/data.
    required = _literal("REQUIRED_ASSETS", ROOT / "scripts" / "check_wheel_assets.py")
    assert f"anki_miner/languages/fa/data/{table}" in required


def _gate():
    spec = importlib.util.spec_from_file_location("check_wheel_assets", ROOT / "scripts" / "check_wheel_assets.py")
    assert spec is not None and spec.loader is not None
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


def _run_gate(monkeypatch, tmp_path: Path, leave_out: str = "") -> int:
    """Run the real gate over a wheel that ships every asset it asks for, minus ``leave_out``."""
    gate = _gate()
    names = gate.fs_assets() | set(gate.REQUIRED_ASSETS)
    names |= {f"anki_miner-0.dist-info/licenses/{notice}" for notice in gate.REQUIRED_WHEEL_LICENSES}
    with zipfile.ZipFile(tmp_path / "anki_miner-0-py3-none-any.whl", "w") as wheel:
        for name in sorted(names - {leave_out}):
            wheel.writestr(name, "")
    monkeypatch.setattr(gate, "DIST_DIR", tmp_path)
    return gate.main()


def test_the_wheel_gate_passes_a_wheel_that_ships_the_tables(monkeypatch, tmp_path):
    # The tables sit outside RESOURCE_DIRS, so the gate has to find them in the
    # repository and the wheel itself, not in the resource-tree listings.
    assert _run_gate(monkeypatch, tmp_path) == 0


@pytest.mark.parametrize("table", ["compound_verbs.tsv", "colloquial.tsv"])
def test_the_wheel_gate_fails_a_wheel_without_a_table(table, monkeypatch, tmp_path, capsys):
    asset = f"anki_miner/languages/fa/data/{table}"
    assert _run_gate(monkeypatch, tmp_path, leave_out=asset) == 1
    assert f"required asset missing from anki_miner-0-py3-none-any.whl: {asset}" in capsys.readouterr().out


def test_the_wheel_gate_pins_the_spec_entry_for_the_tables():
    # check_spec_references_resources() keeps the two build paths from drifting.
    text = (ROOT / "scripts" / "check_wheel_assets.py").read_text(encoding="utf-8")
    assert '\'"anki_miner", "languages", "fa", "data"\'' in text

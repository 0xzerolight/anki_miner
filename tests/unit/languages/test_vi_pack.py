"""The vi pack: pins, the proven four-component closure, the pip extra and the availability probe."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

from anki_miner.languages.vi import availability
from anki_miner.languages.vi.pack import PACK, UNDERTHESEA_EXCLUDES
from anki_miner.services.asr.onnx_pack_installer import _BUNDLE_PYTHON

ROOT = Path(__file__).resolve().parents[3]
RELEASE_KEYS = {
    ("linux", "x86_64"),
    ("linux", "aarch64"),
    ("win32", "AMD64"),
    ("darwin", "arm64"),
    ("darwin", "x86_64"),
}


def _component(name):
    return next(comp for comp in PACK.components if comp.import_name == name)


def test_the_manifest_pins():
    assert PACK.code == "vi" and PACK.requires == () and PACK.approx_download_mb == 10
    assert [comp.import_name for comp in PACK.components] == [
        "underthesea",
        "underthesea_core",
        "joblib",
        "cloudpickle",
    ]
    assert all(comp.required for comp in PACK.components)
    engine = _component("underthesea")
    assert engine.universal.sha256 == "81400f41b75ceff6f80c52b4ad043806c80f5899ec555d8e6374d468a59bce56"
    assert engine.universal.url.endswith("/underthesea-9.5.0-py3-none-any.whl")
    assert engine.universal.member_prefix == "underthesea/" and engine.universal.exclude == UNDERTHESEA_EXCLUDES
    assert engine.sentinels == (
        "__init__.py",
        "pipeline/word_tokenize/models/ws_crf_vlsp2013_20230727/models.bin",
        "pipeline/pos_tag/models/pos_crf_vlsp2013_20230303/models.bin",
    )
    assert _component("joblib").universal.sha256 == "3dbbf9f6e4b592a2357b854608e980fe6390d131d7a82f011a377ef2ebef7aba"
    assert (
        _component("cloudpickle").universal.sha256 == "9acb47f6afd73f60dc1df93bb801b472f05ff42fa6c84167d25cb206be1fbf4a"
    )


def test_the_rust_core_is_pinned_per_platform_at_the_bundle_abi():
    core = _component("underthesea_core")
    assert core.universal is None and core.abi == _BUNDLE_PYTHON == (3, 12)
    assert set(core.per_platform) == RELEASE_KEYS  # linux aarch64 too: every release-matrix key
    for spec in core.per_platform.values():
        assert "underthesea_core-3.3.2-cp312-cp312-" in spec.url and spec.member_prefix == "underthesea_core/"
        assert spec.root_members == ()  # the extension sits inside the package dir
    assert core.per_platform[("linux", "x86_64")].sha256 == (
        "a80ddbedbb3e2092ea9a5c3fba09fb1c66c5ebbc7695c42a4b789933ef58f99b"
    )


def test_every_exclude_names_a_real_path_of_the_installed_engine():
    engine_dir = Path(importlib.util.find_spec("underthesea").origin).parent
    for entry in UNDERTHESEA_EXCLUDES:
        assert (engine_dir / entry.rstrip("/")).exists(), entry


def _pack_shaped_root(tmp_path: Path) -> Path:
    """The installed engine laid out the way pack_installer extracts it: excludes dropped."""
    root = tmp_path / "pack"
    root.mkdir()
    engine_dir = Path(importlib.util.find_spec("underthesea").origin).parent

    def ignore(directory, names):
        relative = Path(directory).relative_to(engine_dir).as_posix()
        prefix = "" if relative == "." else relative + "/"
        dropped = set()
        for name in names:
            path = prefix + name
            if any(path + "/" == entry or path == entry for entry in UNDERTHESEA_EXCLUDES):
                dropped.add(name)
        return dropped | {"__pycache__"}

    shutil.copytree(engine_dir, root / "underthesea", ignore=ignore)
    for name in ("underthesea_core", "joblib", "cloudpickle"):
        (root / name).symlink_to(Path(importlib.util.find_spec(name).origin).parent, target_is_directory=True)
    return root


def test_the_tokenize_and_tag_path_imports_exactly_the_pack(tmp_path):
    """Plan decision 5, pinned: with only the pack root and the stdlib (python -I -S), segmentation and
    the v2.0 POS model run, import no third-party module outside the four components, and the optional
    imports the excludes remove resolve to None instead of raising."""
    root = _pack_shaped_root(tmp_path)
    probe = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(root)!r})
        before = set(sys.modules)
        from pathlib import Path
        import underthesea
        from underthesea import word_tokenize
        import underthesea.pipeline.pos_tag as pos_tag
        from underthesea.models.fast_crf_sequence_tagger import FastCRFSequenceTagger
        model = FastCRFSequenceTagger()
        model.load(str(Path(pos_tag.__file__).parent / "models" / "pos_crf_vlsp2013_20230303"))
        words = word_tokenize("Bác sĩ bây giờ có thể báo tin.", use_token_normalize=False)
        print(words)
        print(model.predict([[word] for word in words]))
        tops = {{name.split(".")[0] for name in set(sys.modules) - before}}
        print(sorted(t for t in tops if t not in sys.stdlib_module_names and not t.startswith("_")))
        print(sorted(k for k in ("agent", "convert_address", "translate", "dependency_parse") if getattr(underthesea, k)))
        """)
    out = subprocess.run([sys.executable, "-I", "-S", "-c", probe], capture_output=True, text=True, check=True)
    words, tags, third_party, optional = out.stdout.splitlines()
    assert words == "['Bác sĩ', 'bây giờ', 'có thể', 'báo', 'tin', '.']"
    assert tags.startswith("['B-N'")
    assert third_party == "['cloudpickle', 'joblib', 'underthesea', 'underthesea_core']"
    assert optional == "[]"


def test_the_pip_extra_and_its_typing_override():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    extras = data["project"]["optional-dependencies"]
    assert extras["vi"] == ["underthesea>=9.5,<9.6"]
    assert "anki-miner[vi]" in extras["languages"]
    ignored = {m for o in data["tool"]["mypy"]["overrides"] if o.get("ignore_missing_imports") for m in o["module"]}
    assert "underthesea.*" in ignored


def test_availability_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(availability, "_importable", lambda _name: False)
    monkeypatch.setattr(availability, "_pack_component_present", lambda _code, _name: False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    reason = availability.vi_missing_reason()
    assert reason.startswith("Vietnamese mining needs underthesea, underthesea_core, joblib, cloudpickle.")
    assert 'pip install "anki-miner[vi]"' in reason and "Settings -> Mining Language" in reason
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert availability.vi_missing_reason() == availability.VI_FROZEN_REASON


def test_availability_is_satisfied_by_the_pack_on_disk(monkeypatch):
    monkeypatch.setattr(availability, "_importable", lambda _name: False)
    monkeypatch.setattr(availability, "_pack_component_present", lambda code, _name: code == "vi")
    assert availability.vi_missing_reason() is None


def test_availability_on_this_machine():
    """The dev venv and PY311 both carry the [vi] extra."""
    assert availability.vi_missing_reason() is None


@pytest.mark.parametrize("name", ["underthesea", "underthesea_core", "joblib", "cloudpickle"])
def test_nothing_in_the_package_imports_the_engine_at_module_level(name):
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "anki_miner" / "languages" / "vi").glob("*.py")
    )
    assert f"\nimport {name}" not in source and f"\nfrom {name}" not in source

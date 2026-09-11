"""The ASR pack manifest is the single source of truth for the engine pins."""

from __future__ import annotations

import re

from anki_miner.languages.pack_spec import ArtifactSpec, DependencyPack
from anki_miner.services.asr.asr_pack import PACK
from anki_miner.services.asr.onnx_pack_installer import _BUNDLE_PYTHON

_RELEASE_PLATFORMS = (("linux", "x86_64"), ("win32", "AMD64"), ("darwin", "arm64"), ("darwin", "x86_64"))

#: The versions requirements.lock resolves for the release build (G5: a user who
#: downloads the pack runs byte-identical packages).
_LOCK_VERSIONS = {
    "ctranslate2": "4.8.0",
    "av": "17.1.0",
    "tokenizers": "0.23.1",
    "yaml": "6.0.3",
    "huggingface_hub": "1.16.1",
    "filelock": "3.29.4",
    "httpx": "0.28.1",
    "httpcore": "1.0.9",
    "h11": "0.16.0",
    "anyio": "4.14.0",
    "faster_whisper": "1.2.1",
}


def _specs(comp) -> list[ArtifactSpec]:
    return [comp.universal] if comp.universal else list(comp.per_platform.values())


def test_the_pack_is_well_formed() -> None:
    assert isinstance(PACK, DependencyPack) and PACK.name == "asr"
    assert PACK.approx_download_mb > 0
    for comp in PACK.components:
        assert comp.required
        assert (comp.universal is None) != (comp.per_platform is None)
        assert comp.sentinels
        for spec in _specs(comp):
            assert spec.url.startswith("https://files.pythonhosted.org/")
            assert len(spec.sha256) == 64
            assert spec.kind == "wheel"
            assert spec.member_prefix == f"{comp.import_name}/"


def test_the_component_set_is_the_computed_closure_in_install_order() -> None:
    assert [c.import_name for c in PACK.components] == list(_LOCK_VERSIONS)


def test_hf_xet_is_deliberately_not_a_component() -> None:
    """huggingface_hub enables xet only when hf_xet's dist-info resolves via
    importlib.metadata (utils/_runtime.py); the pack ships no dist-info and
    neither did the bundle, so the wheel would be inert bytes. The spec still
    excludes it explicitly (tests/unit/test_asr_pack_bundling.py)."""
    assert "hf_xet" not in {c.import_name for c in PACK.components}


def test_every_pin_is_the_release_lock_version() -> None:
    for comp in PACK.components:
        for spec in _specs(comp):
            filename = spec.url.rsplit("/", 1)[-1]
            version = re.match(r"^[A-Za-z0-9_]+-([0-9][^-]*)-", filename).group(1)
            assert version == _LOCK_VERSIONS[comp.import_name], filename


def test_per_platform_tables_cover_the_release_matrix() -> None:
    for comp in PACK.components:
        if comp.per_platform is not None:
            assert set(comp.per_platform) == set(_RELEASE_PLATFORMS), comp.import_name


def test_cp312_pins_match_the_bundle_python_and_abi3_pins_do_not_pin() -> None:
    by_name = {c.import_name: c for c in PACK.components}
    for name in ("ctranslate2", "yaml"):
        assert by_name[name].abi == _BUNDLE_PYTHON
        for spec in _specs(by_name[name]):
            assert "-cp312-cp312-" in spec.url
    for name in ("av", "tokenizers"):
        assert by_name[name].abi is None
        for spec in _specs(by_name[name]):
            assert "-abi3-" in spec.url
    for name in ("huggingface_hub", "filelock", "httpx", "httpcore", "h11", "anyio", "faster_whisper"):
        assert by_name[name].abi is None and by_name[name].universal is not None
        assert by_name[name].universal.url.endswith("-py3-none-any.whl")


def test_the_auditwheel_libs_trees_are_declared_as_directory_root_members() -> None:
    """The extension modules resolve their shared libraries by $ORIGIN/../<pkg>.libs."""
    by_name = {c.import_name: c for c in PACK.components}
    ct2 = by_name["ctranslate2"].per_platform
    assert ct2[("linux", "x86_64")].root_members == ("ctranslate2.libs/",)
    for key in (("win32", "AMD64"), ("darwin", "arm64"), ("darwin", "x86_64")):
        assert ct2[key].root_members == ()
    av = by_name["av"].per_platform
    for key in (("linux", "x86_64"), ("win32", "AMD64")):
        assert av[key].root_members == ("av.libs/",)
    for key in (("darwin", "arm64"), ("darwin", "x86_64")):
        assert av[key].root_members == ()
    for name, comp in by_name.items():
        if name in ("ctranslate2", "av"):
            continue
        for spec in _specs(comp):
            assert spec.root_members == ()


def test_faster_whisper_is_installed_last() -> None:
    """available() needs faster_whisper AND ctranslate2; a half-installed pack must never report available."""
    assert PACK.components[-1].import_name == "faster_whisper"

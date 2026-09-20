"""The pack manifests are the single source of truth for pack pins."""

import importlib
import platform
import sys
from importlib.util import find_spec
from pathlib import Path

import pytest

import anki_miner.languages
from anki_miner.languages import AVAILABLE_LANGUAGES, SHARED_PACK_CODES
from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack
from anki_miner.services.language_pack_installer import combined_download_mb
from anki_miner.services.pack_installer import artifact_for

_RELEASE_PLATFORMS = (("linux", "x86_64"), ("win32", "AMD64"), ("darwin", "arm64"), ("darwin", "x86_64"))
#: Every host a pinned artifact may come from: PyPI, the spaCy model releases,
#: HuSpaCy's tag-pinned HuggingFace model (R31), and the CAMeL Lab's data
#: releases (ar's morphology database, a plain zip).
_URL_PREFIXES = (
    "https://files.pythonhosted.org/",
    "https://github.com/explosion/spacy-models/releases/download/",
    "https://huggingface.co/huspacy/hu_core_news_md/resolve/v3.8.0/",
    "https://github.com/CAMeL-Lab/camel-tools-data/releases/download/",
)


def _packs():
    out = {}
    for code in (*AVAILABLE_LANGUAGES, *SHARED_PACK_CODES):
        if find_spec(f"anki_miner.languages.{code}") is None:
            continue
        if find_spec(f"anki_miner.languages.{code}.pack") is not None:
            out[code] = importlib.import_module(f"anki_miner.languages.{code}.pack").PACK
    return out


def test_ja_has_no_pack():
    assert "ja" not in _packs()


def test_every_pack_is_well_formed():
    packs = _packs()
    # Derived, not hand-listed: every pack.py on disk must be reachable from the
    # two code tuples, which is what load_pack and the .spec derivation walk.
    on_disk = {path.parent.name for path in Path(anki_miner.languages.__file__).parent.glob("*/pack.py")}
    assert set(packs) == on_disk
    for code, pack in packs.items():
        assert isinstance(pack, LanguagePack) and pack.code == code
        assert pack.approx_download_mb > 0
        for comp in pack.components:
            assert (comp.universal is None) != (comp.per_platform is None)
            assert comp.sentinels
            for spec in ([comp.universal] if comp.universal else list(comp.per_platform.values())):
                assert isinstance(spec, ArtifactSpec)
                assert spec.url.startswith(_URL_PREFIXES)
                assert len(spec.sha256) == 64
                # A data zip is flat: its members land in the component dir unchanged.
                assert spec.member_prefix.endswith("/") or (spec.kind == "zip" and spec.member_prefix == "")
                assert spec.inner_sha256 == () or spec.kind == "zip"


def test_per_platform_tables_cover_the_release_matrix():
    for pack in _packs().values():
        for comp in pack.components:
            if comp.per_platform is not None:
                assert set(_RELEASE_PLATFORMS) <= set(comp.per_platform)


def test_the_ko_model_pin_matches_the_retiring_installer():
    """Same bytes users already download; drift here would re-fetch 88 MB."""
    pack = _packs()["ko"]
    model = next(c for c in pack.components if c.import_name == "kiwipiepy_model")
    assert model.universal.sha256 == "498a22f5585e6c4a162423d7557eb3ee3f71cddc6e0aeb2650c50467e85933e2"


def test_the_kiwipiepy_wheels_declare_their_root_level_extension_module():
    """``_kiwipiepy.abi3.so`` ships at the WHEEL ROOT, outside ``kiwipiepy/``.

    ``kiwipiepy/_wrap.py`` does ``import _kiwipiepy``; extracting only the
    package dir promotes a component that imports to ModuleNotFoundError. The
    prefix form covers ``.abi3.so`` and ``.pyd`` without pinning per-OS names.
    """
    ko = _packs()["ko"]
    kiwipiepy = next(c for c in ko.components if c.import_name == "kiwipiepy")
    for spec in kiwipiepy.per_platform.values():
        assert spec.root_members == ("_kiwipiepy.",)


#: Components whose payload has a declared piece outside the package dir: kiwipiepy's extension module,
#: and the pymorphy3 dictionaries' .dist-info (pymorphy3 finds dictionaries only through their entry point).
_ROOT_MEMBER_COMPONENTS = frozenset({"kiwipiepy", "pymorphy3_dicts_ru", "pymorphy3_dicts_uk"})


def test_only_declared_root_members_are_promoted_to_a_pack_root():
    """Every other component keeps its whole payload inside its package dir."""
    for pack in _packs().values():
        for comp in pack.components:
            if comp.import_name in _ROOT_MEMBER_COMPONENTS:
                continue
            for spec in [comp.universal] if comp.universal else list(comp.per_platform.values()):
                assert spec.root_members == ()


#: Every CPython ``pyproject.toml``'s ``requires-python`` admits, and that opencc
#: publishes a wheel for. A new one here is a new sibling in the zh manifest.
_SUPPORTED_PYTHONS = ((3, 11), (3, 12), (3, 13), (3, 14))
#: The five (sys.platform, machine) pairs every opencc sibling pins: the release
#: matrix plus Linux arm64, which users build on and the release does not.
_OPENCC_PLATFORMS = (
    ("linux", "x86_64"),
    ("linux", "aarch64"),
    ("win32", "AMD64"),
    ("darwin", "arm64"),
    ("darwin", "x86_64"),
)


def _opencc_components():
    return [comp for comp in _packs()["zh"].components if comp.import_name == "opencc"]


def test_the_opencc_abi_pin_matches_the_bundle_python():
    """One sibling is the bundle's own interpreter, so a frozen build is served."""
    from anki_miner.services.asr.onnx_pack_installer import _BUNDLE_PYTHON

    assert [comp.abi for comp in _opencc_components()].count(_BUNDLE_PYTHON) == 1


def test_opencc_is_pinned_for_every_python_the_project_supports():
    """``PackComponent`` carries one ABI, so opencc is declared once per CPython.

    Traditional input is cut and read through OpenCC (``zh/tokenizer.py``,
    ``zh/reading.py``), so a pip user on 3.11/3.13/3.14 who takes the in-app
    pack has to be served a wheel rather than silently skipped.
    """
    components = _opencc_components()
    assert sorted(comp.abi for comp in components) == sorted(_SUPPORTED_PYTHONS)
    for comp in components:
        assert comp.required is False, comp.abi
        assert comp.universal is None and set(comp.per_platform) == set(_OPENCC_PLATFORMS), comp.abi


@pytest.mark.parametrize("host", _OPENCC_PLATFORMS, ids=lambda pair: "-".join(pair))
@pytest.mark.parametrize("abi", _SUPPORTED_PYTHONS, ids=lambda abi: f"{abi[0]}.{abi[1]}")
def test_exactly_one_opencc_artifact_resolves_on_each_supported_python(monkeypatch, abi, host):
    """The siblings are alternatives, not additions.

    They all extract to the same ``opencc/`` directory, so a second one
    resolving would download the same package twice into itself.
    """
    # The installer core reads these two module attributes directly.
    monkeypatch.setattr(sys, "version_info", (*abi, 0, "final", 0))
    monkeypatch.setattr(sys, "platform", host[0])
    monkeypatch.setattr(platform, "machine", lambda: host[1])

    resolved = [comp for comp in _opencc_components() if artifact_for(comp) is not None]

    assert [comp.abi for comp in resolved] == [abi]


def test_the_zh_download_figure_counts_one_opencc_wheel():
    """jieba's sdist, pypinyin's wheel and ONE ~2.4 MB opencc wheel."""
    assert _packs()["zh"].approx_download_mb == 23
    assert combined_download_mb("zh") == 23

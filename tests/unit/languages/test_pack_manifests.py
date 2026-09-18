"""The pack manifests are the single source of truth for pack pins."""

import importlib
from importlib.util import find_spec
from pathlib import Path

import anki_miner.languages
from anki_miner.languages import AVAILABLE_LANGUAGES, SHARED_PACK_CODES
from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack

_RELEASE_PLATFORMS = (("linux", "x86_64"), ("win32", "AMD64"), ("darwin", "arm64"), ("darwin", "x86_64"))
#: Every host a pinned artifact may come from: PyPI, the spaCy model releases,
#: and HuSpaCy's tag-pinned HuggingFace model (R31).
_URL_PREFIXES = (
    "https://files.pythonhosted.org/",
    "https://github.com/explosion/spacy-models/releases/download/",
    "https://huggingface.co/huspacy/hu_core_news_md/resolve/v3.8.0/",
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
                assert spec.member_prefix.endswith("/")


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


def test_only_declared_root_members_are_promoted_to_a_pack_root():
    """Every other component keeps its whole payload inside its package dir."""
    for pack in _packs().values():
        for comp in pack.components:
            if comp.import_name == "kiwipiepy":
                continue
            for spec in [comp.universal] if comp.universal else list(comp.per_platform.values()):
                assert spec.root_members == ()


def test_the_opencc_abi_pin_matches_the_bundle_python():
    from anki_miner.services.asr.onnx_pack_installer import _BUNDLE_PYTHON

    zh = _packs()["zh"]
    opencc = next(c for c in zh.components if c.import_name == "opencc")
    assert opencc.abi == _BUNDLE_PYTHON

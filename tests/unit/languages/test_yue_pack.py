"""The yue pack manifest: five platforms, abi3, and what never lands on disk."""

from __future__ import annotations

from pathlib import Path

import pycantonese
import pytest

from anki_miner.languages.pack_spec import LanguagePack
from anki_miner.languages.yue.pack import PACK

PLATFORMS = (
    ("linux", "x86_64"),
    ("linux", "aarch64"),
    ("win32", "AMD64"),
    ("darwin", "arm64"),
    ("darwin", "x86_64"),
)
INSTALLED = Path(pycantonese.__file__).parent


def test_the_pack_is_two_abi3_components_over_five_platforms():
    assert isinstance(PACK, LanguagePack)
    assert PACK.code == "yue"
    assert PACK.requires == ()
    assert [c.import_name for c in PACK.components] == ["pycantonese", "rustling"]
    for component in PACK.components:
        assert component.required is True
        assert component.abi is None  # cp310-abi3: one pin per platform, every CPython
        assert component.universal is None
        assert component.per_platform is not None
        assert set(component.per_platform) == set(PLATFORMS)
        for spec in component.per_platform.values():
            assert spec.kind == "wheel"
            assert spec.url.startswith("https://files.pythonhosted.org/")
            assert "-cp310-abi3-" in spec.url
            assert spec.member_prefix == f"{component.import_name}/"
            assert spec.root_members == ()  # both extension modules live INSIDE the package


def test_the_macos_x86_64_floor_is_read_off_the_tag():
    pycantonese_component = PACK.components[0]
    assert pycantonese_component.per_platform is not None
    assert pycantonese_component.per_platform[("darwin", "x86_64")].min_macos == (10, 12)


@pytest.mark.parametrize("component", PACK.components, ids=lambda c: c.import_name)
def test_every_sentinel_exists_in_the_installed_engine(component):
    root = Path(__import__(component.import_name).__file__).parent
    for sentinel in component.sentinels:
        assert (root / sentinel).exists(), sentinel


def test_the_sentinels_are_platform_neutral():
    # The extension module's name differs per OS (_rust.abi3.so / _rust.pyd), so
    # no sentinel may name one.
    for component in PACK.components:
        assert not any(name.endswith((".so", ".pyd", ".dylib")) for name in component.sentinels)


def _linux_spec(index: int):
    per_platform = PACK.components[index].per_platform
    assert per_platform is not None
    return per_platform[("linux", "x86_64")]


def test_the_excluded_subtrees_exist_and_are_what_they_claim():
    excluded = _linux_spec(0).exclude
    assert excluded == (
        "data/ctcpc/",
        "data/cantomap/",
        "data/common_voice/",
        "word_segmentation/train_segmenter.py",
        "pos_tagging/train_tagger.py",
    )
    for member in excluded:
        assert (INSTALLED / member.rstrip("/")).exists(), member


def test_the_cc_by_licences_travel_inside_the_pack():
    # GPL-3 CantoMap is excluded, so no licenses/ notice is owed; HKCanCor and
    # rime-cantonese are CC BY 4.0 and their notices stay in the pack dir.
    excluded = _linux_spec(0).exclude
    assert "data/cantomap/" in excluded  # GPL-3, never lands
    for member in ("data/hkcancor/LICENSE.txt", "data/rime_cantonese/LICENSE.txt"):
        assert (INSTALLED / member).exists()
        assert not any(member.startswith(entry.rstrip("/")) for entry in excluded)


def test_the_download_estimate_is_the_two_wheels():
    assert sum(len(c.per_platform or {}) for c in PACK.components) == 10
    assert PACK.approx_download_mb == 46

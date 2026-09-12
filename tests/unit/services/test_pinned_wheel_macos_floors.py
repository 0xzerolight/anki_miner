"""A pinned macOS wheel may not demand a newer macOS than the app itself runs on.

macOS is the only platform whose wheel tag encodes an OS floor, and dyld enforces
it absolutely: below it the extension module fails to load, taking the import
with it. Nothing in CI can catch that — every macOS runner in
``.github/release-matrix.json`` is current — and neither shipping path honours a
wheel tag (PyInstaller bakes in whatever the build machine resolved; the packs
download a hard-pinned URL). So the floors are asserted here, statically.

The failure this exists for: ``av==17.1.0``'s arm64 wheel is tagged
``macosx_14_0``, it shipped in v3.1.0, and every Apple Silicon Mac below macOS 14
got ``dlopen(av/_core…so): Symbol not found … Expected in:
AVFoundation.framework`` on any faster-whisper import — with no whisper.cpp leg
on macOS, that is all of transcription.
"""

from __future__ import annotations

import platform
import sys

import pytest

from anki_miner.languages.ko.pack import PACK as KO_PACK
from anki_miner.languages.pack_spec import ArtifactSpec, PackComponent, macos_floor_from_url
from anki_miner.languages.zh.pack import PACK as ZH_PACK
from anki_miner.services._install_common import macos_floor_met
from anki_miner.services.asr import onnx_pack_installer
from anki_miner.services.asr.asr_pack import PACK as ASR_PACK
from anki_miner.services.pack_installer import artifact_for

#: The macOS the app itself requires: the floor of the arm64 wheel of the one
#: dependency no build can drop, ``PyQt6-Qt6`` (``macosx_11_0_arm64``). A pinned
#: wheel above this is a feature the app offers and cannot deliver.
_APP_MACOS_FLOOR = (11, 0)

_PACKS = {"asr": ASR_PACK, "zh": ZH_PACK, "ko": KO_PACK}


def _mac_specs(pack) -> list[tuple[str, str, ArtifactSpec]]:
    """Every (component, arch, spec) triple the pack pins for macOS."""
    out: list[tuple[str, str, ArtifactSpec]] = []
    for comp in pack.components:
        for (os_name, machine), spec in (comp.per_platform or {}).items():
            if os_name == "darwin":
                out.append((comp.import_name, machine, spec))
    return out


def test_the_parser_reads_the_floor_off_the_tag() -> None:
    assert macos_floor_from_url("https://x/av-13.1.0-cp312-cp312-macosx_11_0_arm64.whl") == (11, 0)
    assert macos_floor_from_url("https://x/tokenizers-0.23.1-cp310-abi3-macosx_10_12_x86_64.whl") == (10, 12)
    assert macos_floor_from_url("https://x/pkg-1.0-cp312-cp312-manylinux_2_28_x86_64.whl") is None
    assert macos_floor_from_url("https://x/pkg-1.0-cp312-cp312-win_amd64.whl") is None
    assert macos_floor_from_url("https://x/pkg-1.0.tar.gz") is None


def test_a_multi_platform_tag_reports_its_lowest_floor() -> None:
    """A fat tag loads on the oldest release it names, not the newest."""
    url = "https://x/pkg-1.0-cp312-cp312-macosx_10_9_x86_64.macosx_11_0_arm64.whl"
    assert macos_floor_from_url(url) == (10, 9)


@pytest.mark.parametrize("label", sorted(_PACKS))
def test_every_required_macos_pin_loads_on_the_app_floor(label: str) -> None:
    """The invariant av 17.1.0 broke. Optional components may sit higher."""
    required = {comp.import_name for comp in _PACKS[label].components if comp.required}
    for name, machine, spec in _mac_specs(_PACKS[label]):
        if name not in required:
            continue
        floor = spec.min_macos
        assert floor is not None, f"{label}/{name}/{machine}: macOS pin with no readable tag"
        assert (
            floor <= _APP_MACOS_FLOOR
        ), f"{label}/{name}/{machine}: needs macOS {floor}, the app runs on {_APP_MACOS_FLOOR}"


@pytest.mark.parametrize("label", sorted(_PACKS))
def test_no_non_macos_pin_claims_a_floor(label: str) -> None:
    """Guards the parser against matching something that is not a macOS tag."""
    for comp in _PACKS[label].components:
        if comp.universal is not None:
            assert comp.universal.min_macos is None, comp.import_name
        for (os_name, machine), spec in (comp.per_platform or {}).items():
            if os_name != "darwin":
                assert spec.min_macos is None, f"{comp.import_name}/{os_name}/{machine}"


def test_the_onnxruntime_vad_pin_is_the_one_declared_exception() -> None:
    """Silence removal is optional and onnxruntime ships no arm64 wheel at the app
    floor, so it is allowed to sit above it — but only because it is *gated*
    (``_current_spec`` declines it below the floor). Raising this is a deliberate
    edit here, never a silent side effect of a version bump."""
    arm = onnx_pack_installer._WHEELS[("darwin", "arm64")]
    assert arm.min_macos == (13, 0)
    for (os_name, _machine), spec in onnx_pack_installer._WHEELS.items():
        if os_name != "darwin":
            assert spec.min_macos is None


class TestMacosFloorMet:
    def test_no_floor_and_non_darwin_hosts_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        assert macos_floor_met(None) is True
        assert macos_floor_met((26, 0)) is True

    @pytest.mark.parametrize(
        ("release", "floor", "expected"),
        [
            ("14.2.1", (14, 0), True),
            ("14.0", (14, 0), True),
            ("13.7.2", (14, 0), False),
            ("13.0", (13, 0), True),
            ("12.7.6", (13, 0), False),
            ("11.0", (11, 0), True),
            ("10.15.7", (11, 0), False),
            ("15", (14, 0), True),
        ],
    )
    def test_a_darwin_host_is_compared_against_the_floor(
        self, monkeypatch: pytest.MonkeyPatch, release: str, floor: tuple[int, int], expected: bool
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform, "mac_ver", lambda: (release, ("", "", ""), "arm64"))
        assert macos_floor_met(floor) is expected

    @pytest.mark.parametrize("release", ["10.16", "", "not-a-version"])
    def test_an_unreadable_or_shimmed_release_fails_open(self, monkeypatch: pytest.MonkeyPatch, release: str) -> None:
        """10.16 is Big Sur+ masked by a pre-11 SDK, not an old machine; an empty
        or junk string is no evidence either way. Hiding a working feature on a
        surprising version string is the worse failure."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform, "mac_ver", lambda: (release, ("", "", ""), "arm64"))
        assert macos_floor_met((14, 0)) is True


class TestSelectionDeclinesAnUnloadableWheel:
    """A wheel dyld would refuse is not offered — the pack row hides exactly as it
    does on a platform with no pin at all, instead of downloading tens of MB that
    end in an ImportError at engine startup."""

    def _fake_mac(self, monkeypatch: pytest.MonkeyPatch, release: str) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform, "machine", lambda: "arm64")
        monkeypatch.setattr(platform, "mac_ver", lambda: (release, ("", "", ""), "arm64"))

    def _component(self) -> PackComponent:
        return PackComponent(
            import_name="pkg",
            required=True,
            sentinels=("__init__.py",),
            per_platform={
                ("darwin", "arm64"): ArtifactSpec(
                    url="https://x/pkg-1.0-cp312-cp312-macosx_14_0_arm64.whl",
                    sha256="0" * 64,
                    kind="wheel",
                    member_prefix="pkg/",
                ),
            },
        )

    def test_artifact_for_declines_below_the_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._fake_mac(monkeypatch, "13.6")
        assert artifact_for(self._component()) is None

    def test_artifact_for_returns_the_wheel_at_the_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._fake_mac(monkeypatch, "14.0")
        spec = artifact_for(self._component())
        assert spec is not None and spec.min_macos == (14, 0)

    def test_the_onnx_pack_is_not_offered_below_its_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(onnx_pack_installer, "_BUNDLE_PYTHON", sys.version_info[:2])
        self._fake_mac(monkeypatch, "12.7")
        assert onnx_pack_installer.onnx_pack_supported() is False

    def test_the_onnx_pack_is_offered_at_its_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(onnx_pack_installer, "_BUNDLE_PYTHON", sys.version_info[:2])
        self._fake_mac(monkeypatch, "13.0")
        assert onnx_pack_installer.onnx_pack_supported() is True

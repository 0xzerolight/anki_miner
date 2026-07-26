"""Guards on the vendored yt-dlp pin (.github/ytdlp-pin.json).

The pin is consumed by two places that must not drift — ``.github/workflows/release.yml``
and ``scripts/release_preflight.sh`` — and by ``scripts/check_ytdlp_pin.py``, which
gates freshness at build time. These tests are the offline half of that gate: shape,
asset choice, and digest sanity, with no network.

The asset-choice assertion is the load-bearing one. The bare ``yt-dlp`` release asset
is a zipimport archive whose shebang runs the *system* ``python3`` and which carries
no ``curl_cffi``; vendoring it would make a packaged app depend on a host Python it
does not ship and would silently lose YouTube impersonation.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PIN_PATH = _REPO_ROOT / ".github" / "ytdlp-pin.json"

#: Assets that are standalone builds. Anything else (notably the bare "yt-dlp"
#: zipapp) must never be vendored.
_ALLOWED_ASSETS = {
    "linux": {"yt-dlp_linux", "yt-dlp_linux_aarch64", "yt-dlp_musllinux"},
    "windows": {"yt-dlp.exe", "yt-dlp_arm64.exe", "yt-dlp_x86.exe"},
    "macos": {"yt-dlp_macos", "yt-dlp_macos_legacy"},
}


@pytest.fixture(scope="module")
def pin() -> dict:
    return json.loads(_PIN_PATH.read_text(encoding="utf-8"))


def test_pin_file_exists() -> None:
    assert _PIN_PATH.is_file(), f"missing {_PIN_PATH}"


def test_version_is_a_ytdlp_date_tag(pin: dict) -> None:
    """yt-dlp tags are YYYY.MM.DD, which is what dates the pin for the staleness gate."""
    datetime.strptime(pin["version"], "%Y.%m.%d")


def test_covers_every_release_matrix_platform(pin: dict) -> None:
    """Every leg in release-matrix.json needs an asset, or its bundle ships no yt-dlp."""
    matrix = json.loads((_REPO_ROOT / ".github" / "release-matrix.json").read_text(encoding="utf-8"))
    # macos-arm64 and macos-intel both take the universal2 "macos" entry.
    needed = {entry["platform"].split("-")[0] for entry in matrix}
    assert needed <= set(pin["assets"]), f"pin is missing legs: {needed - set(pin['assets'])}"


@pytest.mark.parametrize("leg", ["linux", "windows", "macos"])
def test_asset_is_a_standalone_build(pin: dict, leg: str) -> None:
    asset = pin["assets"][leg]["asset"]
    assert asset != "yt-dlp", (
        f"assets.{leg} pins the zipapp asset. It shebangs the system python3 (absent "
        "from a packaged app) and carries no curl_cffi, so impersonation would break."
    )
    assert asset in _ALLOWED_ASSETS[leg], f"assets.{leg}.asset={asset!r} is not a known standalone build"


@pytest.mark.parametrize("leg", ["linux", "windows", "macos"])
def test_digest_is_a_sha256(pin: dict, leg: str) -> None:
    sha = pin["assets"][leg]["sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", sha), f"assets.{leg}.sha256 is not a lowercase 64-hex digest"


@pytest.mark.parametrize("leg", ["linux", "windows", "macos"])
def test_install_as_matches_the_resolver_lookup(pin: dict, leg: str) -> None:
    """The bundled filename must be what ``bundled_name('yt-dlp')`` looks for."""
    expected = "yt-dlp.exe" if leg == "windows" else "yt-dlp"
    assert pin["assets"][leg]["install_as"] == expected


def test_digests_are_distinct(pin: dict) -> None:
    """A copy-paste slip between legs would ship the wrong OS's binary."""
    digests = [entry["sha256"] for entry in pin["assets"].values()]
    assert len(digests) == len(set(digests))


def test_max_age_days_is_sane(pin: dict) -> None:
    """Long enough not to fire on a healthy pin, short enough to catch neglect."""
    assert 30 <= pin["max_age_days"] <= 180


def test_release_workflow_and_preflight_both_read_the_pin() -> None:
    """Neither consumer may hardcode a version or digest of its own.

    ffmpeg's and alass's pins are duplicated across release.yml and
    release_preflight.sh; the whole point of this file is not to repeat that.
    """
    workflow = (_REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    preflight = (_REPO_ROOT / "scripts" / "release_preflight.sh").read_text(encoding="utf-8")
    for name, text in (("release.yml", workflow), ("release_preflight.sh", preflight)):
        assert "ytdlp-pin.json" in text, f"{name} does not read .github/ytdlp-pin.json"


def test_spec_bundles_the_vendor_dir() -> None:
    """The spec must actually pick vendor/yt-dlp up, or the pin is inert."""
    spec = (_REPO_ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert 'os.path.join(project_root, "vendor", "yt-dlp")' in spec
    assert "ytdlp_binaries" in spec
    # ...and the Python package must stay excluded: the app only ever spawns the
    # executable, so collecting the module was ~16 MB of dead weight.
    assert '"yt_dlp",' in spec

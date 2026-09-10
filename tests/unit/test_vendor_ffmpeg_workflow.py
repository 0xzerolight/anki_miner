"""Provenance checks for the vendored FFmpeg build.

release.yml and scripts/release_preflight.sh fetch the same Linux asset; a pin
that drifts between them builds the release from a different ffmpeg than the one
the preflight proved. The shared build has more to keep in step than the static
one did: the executables are inert without the libav*/libsw* sonames beside them
and the $ORIGIN rpath that finds them.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"
_PREFLIGHT = _ROOT / "scripts" / "release_preflight.sh"
_SPEC = _ROOT / "anki_miner.spec"
_LICENCE = _ROOT / "licenses" / "ffmpeg" / "README.md"

_ASSET_RE = re.compile(r"ffmpeg-n[0-9][^/\"' ]*?-(?:linux64|win64)-gpl[^/\"' ]*?\.(?:tar\.xz|zip)")
_PATCHELF = "patchelf --force-rpath --set-rpath '$ORIGIN' vendor/ffmpeg/ffmpeg vendor/ffmpeg/ffprobe"


def _release() -> str:
    return _RELEASE.read_text(encoding="utf-8")


def _preflight() -> str:
    return _PREFLIGHT.read_text(encoding="utf-8")


def test_linux_and_windows_fetch_the_shared_variant() -> None:
    assets = [match.group(0) for match in _ASSET_RE.finditer(_release())]

    assert assets, "no BtbN asset found in release.yml — the scan is broken, not the workflow"
    for asset in assets:
        assert "-gpl-shared-" in asset, (
            f"{asset}: the static variant ships a second full copy of every codec. "
            "Vendor the gpl-shared asset so ffmpeg and ffprobe share one set of libraries."
        )


def test_the_linux_pin_matches_the_preflight_mirror() -> None:
    workflow_url = re.search(r'URL="(https://github\.com/BtbN/[^"]+)"', _release())
    workflow_sha = re.search(r'SHA256="([0-9a-f]{64})"', _release())
    preflight_url = re.search(r'FFMPEG_URL="([^"]+)"', _preflight())
    preflight_sha = re.search(r'FFMPEG_SHA256="([0-9a-f]{64})"', _preflight())

    assert workflow_url and workflow_sha and preflight_url and preflight_sha
    assert workflow_url.group(1) == preflight_url.group(1)
    assert workflow_sha.group(1) == preflight_sha.group(1)


def test_both_fetchers_vendor_one_file_per_soname() -> None:
    for name, text in (("release.yml", _release()), ("release_preflight.sh", _preflight())):
        assert "*.so.*.*) continue ;;" in text, (
            f"{name}: copy one file per soname (libavcodec.so.62) and skip the fully "
            "versioned name — they are the same bytes and would ship twice."
        )
        assert "cp -L " in text, f"{name}: BtbN ships the soname as a symlink; copy its target."


def test_both_fetchers_give_the_executables_an_origin_rpath() -> None:
    for name, text in (("release.yml", _release()), ("release_preflight.sh", _preflight())):
        assert _PATCHELF in text, (
            f"{name}: BtbN links the executables with a malformed rpath, so the vendored "
            "libraries only resolve once it is rewritten to $ORIGIN."
        )


def test_patchelf_is_available_to_both_fetchers() -> None:
    linux_deps = _release().split("Install system dependencies (Linux)", 1)[1].split("- name:", 1)[0]

    assert "patchelf" in linux_deps
    assert "command -v patchelf" in _preflight()


def test_windows_vendors_the_dlls_beside_the_executables() -> None:
    windows_step = _release().split("Fetch shared ffmpeg (Windows)", 1)[1].split("# ----", 1)[0]

    assert "-Filter *.dll" in windows_step


def test_licence_notice_names_the_shared_variant() -> None:
    text = _LICENCE.read_text(encoding="utf-8")

    assert "gpl-shared-8.1" in text
    assert "static build of" not in text, "Linux and Windows no longer ship a static FFmpeg build"


def test_spec_attaches_vendored_ffmpeg_after_analysis() -> None:
    spec = _SPEC.read_text(encoding="utf-8")

    assert "a.binaries += ffmpeg_toc" in spec, (
        "The shared libav*/libsw* files must never go through Analysis: its dependency "
        "walk collects a second copy of each at the _internal root, plus the build host's "
        "own ffmpeg dependency closure."
    )
    assert spec.index("a = Analysis(") < spec.index("a.binaries += ffmpeg_toc")
    assert (
        '_ffmpeg_is_static = sys.platform == "darwin"' in spec
    ), "macOS has no shared build and keeps the Analysis path (.app layout + codesign step)."

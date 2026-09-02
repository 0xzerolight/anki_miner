"""Pins the release asset naming scheme across every producer.

Five assets ship per release. Four follow one template; the .deb follows
Debian's mandated shape. Each name is built in a different place — the
AppImage in a shell script, the installer in an Inno Setup script, the .deb
and the macOS tarballs in release.yml — and nothing but this test stops one
of them drifting. The macOS tarballs shipped unversioned for the whole 2.x
line for exactly that reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"
RELEASE_MATRIX = REPO_ROOT / ".github" / "release-matrix.json"
APPIMAGE_SH = REPO_ROOT / "packaging" / "appimage" / "build-appimage.sh"
INNO_ISS = REPO_ROOT / "packaging" / "innosetup" / "anki_miner.iss"
NFPM_YAML = REPO_ROOT / "packaging" / "nfpm.yaml"
PREFLIGHT_SH = REPO_ROOT / "scripts" / "release_preflight.sh"

# The four slugs the naming template is parameterised on, keyed by the
# release-matrix platform they belong to.
EXPECTED_SLUGS = {
    "linux": "Linux-x86_64",
    "windows": "Windows-x86_64",
    "macos-arm64": "macOS-arm64",
    "macos-intel": "macOS-x86_64",
}

# Debian filename shape: <package>_<version>_<arch>.deb. Not negotiable —
# dpkg-name and apt repos assume it, so the .deb is the one asset that does
# not follow the AnkiMiner-<version>-<slug> template.
DEB_NAME = "anki-miner_{version}_amd64.deb"


def render_asset_names(version: str) -> set[str]:
    """The exact set of files a release for *version* must publish."""
    return {
        f"AnkiMiner-{version}-Linux-x86_64.AppImage",
        f"AnkiMiner-{version}-Windows-x86_64-Setup.exe",
        f"AnkiMiner-{version}-macOS-arm64.tar.gz",
        f"AnkiMiner-{version}-macOS-x86_64.tar.gz",
        DEB_NAME.format(version=version),
    }


def test_render_asset_names_all_carry_the_version():
    """Every published asset name embeds the version. This is the whole point."""
    for name in render_asset_names("9.9.9"):
        assert "9.9.9" in name, f"{name} does not carry the version"


def test_release_matrix_declares_every_asset_slug():
    entries = json.loads(RELEASE_MATRIX.read_text(encoding="utf-8"))
    got = {e["platform"]: e["asset_slug"] for e in entries}
    assert got == EXPECTED_SLUGS


def test_release_matrix_artifact_names_are_unversioned():
    """CI artifact names must stay version-free: artifact_size_baseline.json is
    keyed on them and artifact_size_report.py fails when a key goes missing."""
    entries = json.loads(RELEASE_MATRIX.read_text(encoding="utf-8"))
    for entry in entries:
        assert not re.search(r"\d+\.\d+\.\d+", entry["artifact_name"])


def test_release_yml_builds_every_asset_path_from_the_template():
    text = RELEASE_YML.read_text(encoding="utf-8")
    version = "${{ steps.version.outputs.version }}"
    slug = "${{ matrix.asset_slug }}"
    for expected in (
        f"dist/AnkiMiner-{version}-{slug}.AppImage",
        f"dist/AnkiMiner-{version}-{slug}-Setup.exe",
        f"dist/AnkiMiner-{version}-{slug}.tar.gz",
        f"dist/anki-miner_{version}_amd64.deb",
    ):
        assert expected in text, f"release.yml no longer builds {expected}"


def test_release_yml_keeps_if_no_files_found_error_on_every_upload():
    """The suffix-based release glob cannot catch a drifted filename; these
    three per-OS uploads are the only literal-name guard in CI."""
    # Match the directive, not the prose — the comment above the upload steps
    # quotes "if-no-files-found: error" too.
    lines = RELEASE_YML.read_text(encoding="utf-8").splitlines()
    directives = [ln for ln in lines if ln.strip() == "if-no-files-found: error"]
    assert len(directives) == 3


def test_appimage_script_matches_the_linux_slug():
    text = APPIMAGE_SH.read_text(encoding="utf-8")
    assert "AnkiMiner-${VERSION}-Linux-x86_64.AppImage" in text


def test_inno_output_basename_matches_the_windows_slug():
    text = INNO_ISS.read_text(encoding="utf-8")
    assert "OutputBaseFilename=AnkiMiner-{#AppVersion}-Windows-x86_64-Setup" in text


def test_deb_filename_matches_nfpm_metadata():
    """release.yml passes --target by hand; nfpm.yaml carries the package
    metadata. Nothing else asserts the two agree."""
    nfpm = NFPM_YAML.read_text(encoding="utf-8")
    assert "name: anki-miner" in nfpm
    assert "arch: amd64" in nfpm
    rendered = DEB_NAME.format(version="${{ steps.version.outputs.version }}")
    assert f"dist/{rendered}" in RELEASE_YML.read_text(encoding="utf-8")
    assert "dist/anki-miner_${VERSION}_amd64.deb" in PREFLIGHT_SH.read_text(encoding="utf-8")


def test_every_published_asset_is_claimed_by_exactly_one_update_target():
    """The macOS drift bug in reverse: if a producer renames an asset and the
    updater's glob is not updated, _pick_asset silently returns None and the
    banner degrades to the release page with no error anywhere."""
    import fnmatch

    from anki_miner.services.update_checker import _TARGET_PATTERNS

    for name in render_asset_names("9.9.9"):
        matched = [
            target for target, patterns in _TARGET_PATTERNS.items() if any(fnmatch.fnmatch(name, p) for p in patterns)
        ]
        assert len(matched) == 1, f"{name} matched {matched}, expected exactly one target"


def test_every_update_target_is_satisfied_by_a_published_asset():
    import fnmatch

    from anki_miner.services.update_checker import _TARGET_PATTERNS

    names = render_asset_names("9.9.9")
    for target, patterns in _TARGET_PATTERNS.items():
        assert any(
            fnmatch.fnmatch(name, p) for p in patterns for name in names
        ), f"target {target} matches no published asset"

"""Static provenance checks for the manually dispatched libmpv workflow.

The SHA-pin check is repo-wide rather than libmpv-only: a floating tag can be
re-pointed at any commit, so an unpinned ``uses:`` is a supply-chain hole wherever it
sits. ytdlp-cdn-canary.yml shipped on floating tags and was the only file out of step.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_WORKFLOWS_DIR = Path(__file__).parents[2] / ".github" / "workflows"
_WORKFLOW_PATH = _WORKFLOWS_DIR / "vendor-libmpv.yml"
_RELEASE_WORKFLOW_PATH = _WORKFLOWS_DIR / "release.yml"
_ROOT = Path(__file__).parents[2]
_PREFLIGHT_PATH = _ROOT / "scripts" / "release_preflight.sh"
_ASSETS_DOWNLOADS = "https://github.com/0xzerolight/anki_miner_assets/releases/download/"

_WORKFLOW_PATHS = sorted([*_WORKFLOWS_DIR.glob("*.yml"), *_WORKFLOWS_DIR.glob("*.yaml")])


@pytest.mark.parametrize("workflow", _WORKFLOW_PATHS, ids=[path.name for path in _WORKFLOW_PATHS])
def test_workflow_actions_are_sha_pinned(workflow: Path) -> None:
    uses_lines = [line.strip() for line in workflow.read_text(encoding="utf-8").splitlines() if "uses:" in line]

    assert uses_lines, f"{workflow.name} declares no `uses:` — the scan is broken, not the workflow"
    for line in uses_lines:
        assert re.search(r"\buses:\s+\S+@[0-9a-f]{40}\s+#\s+\S+", line), (
            f"{workflow.name}: {line} — pin the action to a full commit SHA with a "
            "`# vX.Y.Z` comment. A floating tag can be re-pointed at any commit."
        )


def test_vendor_libmpv_windows_checksum_fails_closed() -> None:
    workflow = _WORKFLOW_PATH.read_text(encoding="utf-8")
    mirror_step = workflow.split("- name: Mirror zhongfly", 1)[1].split("- name: Audit libmpv", 1)[0]

    assert 'gh release download "$TAG"' in mirror_step
    assert '-p "*sha256*"' in mirror_step
    assert 'grep -hF "$ASSET" *sha256* | sha256sum -c -' in mirror_step
    assert "|| true" not in mirror_step
    assert "WARNING: no upstream sha256" not in mirror_step


def test_vendor_libmpv_macos_includes_pinned_libopus_notice() -> None:
    workflow = _WORKFLOW_PATH.read_text(encoding="utf-8")
    macos_step = workflow.split("- name: Bundle libmpv + dylib closure from Homebrew", 1)[1].split(
        "- name: Smoke-load the bundled dylib", 1
    )[0]

    assert "OPUS_VERSION=\"$(brew list --versions opus | awk '{print $2}')\"" in macos_step
    assert "xiph/opus/v${OPUS_VERSION}/COPYING" in macos_step
    assert "out/COPYING.libopus" in macos_step
    notice = (_ROOT / "licenses" / "libmpv" / "COPYING.libopus").read_text(encoding="utf-8")
    assert "Copyright 2001-2023 Xiph.Org" in notice
    assert "Redistributions in binary form must reproduce" in notice


def test_windows_vulkan_loader_is_paired_with_its_license() -> None:
    workflow = _RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
    loader_step = workflow.split("- name: Bundle the Vulkan loader next to libmpv (Windows)", 1)[1].split(
        "- name: Build Vulkan pywhispercpp wheel (Linux)", 1
    )[0]

    assert "vulkan-1.dll" in loader_step
    assert "licenses\\vulkan-loader\\LICENSE.txt" in loader_step
    assert "Test-Path -LiteralPath $license" in loader_step
    assert '"licenses", "vulkan-loader"' in (_ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    notice = (_ROOT / "licenses" / "vulkan-loader" / "LICENSE.txt").read_text(encoding="utf-8")
    assert "Apache License" in notice
    assert "Version 2.0, January 2004" in notice


def test_libmpv_comes_from_the_assets_repo_and_the_preflight_mirrors_linux() -> None:
    # The assets repo keeps vendor downloads out of this repo's release counts.
    # RELEASING.md's bump procedure names release.yml first, so the preflight
    # pin is the one that drifts.
    release = _RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
    preflight = _PREFLIGHT_PATH.read_text(encoding="utf-8")
    linux_step = release.split("- name: Fetch libmpv (Linux)", 1)[1].split("- name:", 1)[0]
    base = re.search(r'BASE="([^"]+)"', linux_step)
    sha = re.search(r'SHA256="([0-9a-f]{64})"', linux_step)
    preflight_url = re.search(r'LIBMPV_URL="([^"]+)"', preflight)
    preflight_sha = re.search(r'LIBMPV_SHA256="([0-9a-f]{64})"', preflight)

    assert base and sha and preflight_url and preflight_sha
    assert base.group(1).startswith(_ASSETS_DOWNLOADS)
    assert preflight_url.group(1) == f"{base.group(1)}/libmpv-linux-x86_64.tar.gz"
    assert sha.group(1) == preflight_sha.group(1)
    assert "github.repository }}/releases/download/vendor-libmpv" not in release


def test_vendor_libmpv_cannot_publish_into_this_repo() -> None:
    # Vendor releases live in anki_miner_assets so they stay out of this repo's
    # download counts. A read-only token makes a stray `gh release create` here fail.
    workflow = _WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "contents: write" not in workflow
    assert "-R 0xzerolight/anki_miner_assets" in workflow

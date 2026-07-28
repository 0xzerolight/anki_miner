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

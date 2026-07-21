"""Static provenance checks for the manually dispatched libmpv workflow."""

from __future__ import annotations

import re
from pathlib import Path

_WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "vendor-libmpv.yml"


def test_vendor_libmpv_actions_are_sha_pinned() -> None:
    uses_lines = [line.strip() for line in _WORKFLOW_PATH.read_text(encoding="utf-8").splitlines() if "uses:" in line]

    assert uses_lines
    for line in uses_lines:
        assert re.search(r"\buses:\s+\S+@[0-9a-f]{40}\s+#\s+\S+", line), line


def test_vendor_libmpv_windows_checksum_fails_closed() -> None:
    workflow = _WORKFLOW_PATH.read_text(encoding="utf-8")
    mirror_step = workflow.split("- name: Mirror zhongfly", 1)[1].split("- name: Audit libmpv", 1)[0]

    assert 'gh release download "$TAG"' in mirror_step
    assert '-p "*sha256*"' in mirror_step
    assert 'grep -hF "$ASSET" *sha256* | sha256sum -c -' in mirror_step
    assert "|| true" not in mirror_step
    assert "WARNING: no upstream sha256" not in mirror_step

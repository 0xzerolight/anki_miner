"""Every ubuntu CI job that runs pytest must install the Qt system libraries.

``pytest-qt`` imports ``QtGui`` in ``pytest_configure`` — before collection — and
``tests/conftest.py``'s autouse ``_no_real_ytdlp_autoupdate`` imports ``MainWindow`` at the
setup of *every* test. So any pytest invocation in this repo needs libEGL and friends
regardless of which tests are selected; ``-p no:qt`` is not enough to dodge it.

``.github/workflows/ytdlp-cdn-canary.yml`` shipped without the apt step that ci.yml has, so
its first weekly run died with ``ImportError: libEGL.so.1`` inside ``pytest_configure``
(pytest INTERNALERROR, exit 3) and then filed a canned "CDN host drifted off the allowlist"
issue having tested nothing at all (issue #104).

Two deliberate holes, so a later reader can tell an exemption from a regression:

* pytest reached through a wrapper script is invisible to a ``run:`` text scan.
* a list-form ``runs-on: [ubuntu-latest]`` is skipped rather than guessed at.

The ``runs-on`` scoping is load-bearing the other way too: ``release.yml`` builds on
``runs-on: ${{ matrix.os }}`` across four OSes, and a future pytest job there must not be
handed an unfixable demand for ``apt-get``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_WORKFLOWS_DIR = Path(__file__).parents[2] / ".github" / "workflows"

#: A pytest invocation inside a ``run:`` block, including the ``python -m pytest`` spelling.
_PYTEST_RE = re.compile(r"(?:^|[\s;&|(])(?:python3?\s+-m\s+)?pytest(?:\s|$)")

#: The Qt system library whose absence is the exact failure this test prevents.
_QT_SYSTEM_LIB = "libegl1"


def _ubuntu_jobs_running_pytest() -> list[tuple[str, str, dict[str, Any]]]:
    """Return ``(workflow_name, job_name, job)`` for every ubuntu job that invokes pytest."""
    found: list[tuple[str, str, dict[str, Any]]] = []
    paths = sorted([*_WORKFLOWS_DIR.glob("*.yml"), *_WORKFLOWS_DIR.glob("*.yaml")])
    for path in paths:
        # YAML 1.1 parses the `on:` key as boolean True; harmless, we only read `jobs`.
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_name, job in (document.get("jobs") or {}).items():
            runs_on = job.get("runs-on")
            if not isinstance(runs_on, str) or not runs_on.startswith("ubuntu"):
                continue
            steps = job.get("steps") or []
            if any(_PYTEST_RE.search(step.get("run") or "") for step in steps):
                found.append((path.name, job_name, job))
    return found


_PYTEST_JOBS = _ubuntu_jobs_running_pytest()


def test_the_scan_finds_something() -> None:
    """A silently-empty scan would make every assertion below vacuously pass."""
    assert _PYTEST_JOBS, (
        f"no ubuntu job invoking pytest found under {_WORKFLOWS_DIR} — the scan is broken "
        "(renamed key, changed workflow shape), not the repo"
    )


@pytest.mark.parametrize(
    ("workflow", "job_name", "job"),
    _PYTEST_JOBS,
    ids=[f"{workflow}::{job_name}" for workflow, job_name, _ in _PYTEST_JOBS],
)
def test_pytest_job_installs_qt_system_deps(workflow: str, job_name: str, job: dict[str, Any]) -> None:
    installs = [step for step in job["steps"] if _QT_SYSTEM_LIB in (step.get("run") or "")]

    assert installs, (
        f"{workflow} job {job_name!r} runs pytest but never installs {_QT_SYSTEM_LIB}. "
        "pytest-qt imports QtGui in pytest_configure, so pytest cannot even start without "
        "the Qt system libraries — the run dies with INTERNALERROR before collection "
        "(issue #104). Add ci.yml's 'Install Qt system dependencies' step to this job."
    )

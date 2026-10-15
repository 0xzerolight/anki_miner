"""pypdf is a declared core dependency with one release pin, and the bundle names it (TS-05)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_REPO = Path(__file__).resolve().parents[3]


def test_pypdf_is_a_core_dependency():
    project = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = {canonicalize_name(Requirement(raw).name) for raw in project["dependencies"]}
    assert "pypdf" in names


def test_pypdf_has_one_exact_release_constraint():
    pins = [
        line.strip()
        for line in (_REPO / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.strip().lower().startswith("pypdf==")
    ]
    assert len(pins) == 1


def test_the_bundle_spec_names_pypdf():
    assert '"pypdf",' in (_REPO / "anki_miner.spec").read_text(encoding="utf-8")

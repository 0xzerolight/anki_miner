"""rapidfuzz is a declared core dependency (the aligner imports it at module level)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_REPO = Path(__file__).resolve().parents[4]


def test_rapidfuzz_is_a_core_dependency():
    project = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = {canonicalize_name(Requirement(raw).name) for raw in project["dependencies"]}
    assert "rapidfuzz" in names


def test_rapidfuzz_has_one_exact_release_constraint():
    pins = [
        line.strip()
        for line in (_REPO / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.strip().lower().startswith("rapidfuzz==")
    ]
    assert len(pins) == 1


def test_opcodes_alignment_shape():
    from rapidfuzz.distance import Levenshtein

    tags = [op[0] for op in Levenshtein.opcodes("qabxcd", "abycdf")]
    assert tags == ["delete", "equal", "replace", "equal", "insert"]

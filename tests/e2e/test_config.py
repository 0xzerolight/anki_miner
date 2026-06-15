"""Validation tests for ``E2EConfig`` curation knobs.

The behavioural defaults / env-override / frozen / runs_root tests live in
``test_anki_gateway.py``'s ``--- config ---`` section. This file isolates the
``__post_init__`` curation-policy guards added so a misconfigured run can't
silently mine nothing.
"""

import dataclasses

import pytest

from tests.e2e.config import E2EConfig


def test_first_n_policy_with_zero_cap_raises():
    """policy 'first_n' with first_n <= 0 would behave like 'none' silently."""
    with pytest.raises(ValueError, match="first_n > 0"):
        E2EConfig(curation_policy="first_n", first_n=0)


def test_first_n_policy_with_negative_cap_raises():
    with pytest.raises(ValueError, match="first_n > 0"):
        E2EConfig(curation_policy="first_n", first_n=-3)


def test_first_n_policy_with_positive_cap_ok():
    cfg = E2EConfig(curation_policy="first_n", first_n=5)
    assert cfg.first_n == 5
    assert cfg.curation_policy == "first_n"


def test_unknown_policy_raises():
    with pytest.raises(ValueError, match="curation_policy must be one of"):
        E2EConfig(curation_policy="everything")  # type: ignore[arg-type]


@pytest.mark.parametrize("policy", ["all", "none"])
def test_all_and_none_policies_ignore_first_n(policy):
    """'all'/'none' don't depend on first_n, so first_n=0 is fine for them."""
    cfg = E2EConfig(curation_policy=policy, first_n=0)
    assert cfg.curation_policy == policy


def test_replace_into_bad_policy_raises():
    """``dataclasses.replace`` re-runs ``__post_init__``, so the guard still fires."""
    cfg = E2EConfig()
    with pytest.raises(ValueError, match="first_n > 0"):
        dataclasses.replace(cfg, curation_policy="first_n")  # first_n stays at default 0

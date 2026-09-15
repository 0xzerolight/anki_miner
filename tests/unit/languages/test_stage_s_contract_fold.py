"""Stage S contract cases for the comparison fold (S3), over every registered profile."""

from __future__ import annotations

import pytest

from anki_miner.languages.registry import available_languages, get_profile

CODES = sorted(available_languages())

_FOLD_SAMPLES = ("食べた", "Hund", "The Cat", "Straße", "Café", "  spaced  ", "頭髮", "乾", "裏面", "記著", "麼")


@pytest.mark.parametrize("code", CODES)
def test_comparison_and_index_folds_are_idempotent(code):
    profile = get_profile(code)
    folds = [profile.dict_keys.fold_term] + ([profile.dedup_fold] if profile.dedup_fold is not None else [])
    for fold in folds:
        for sample in _FOLD_SAMPLES:
            assert fold(fold(sample)) == fold(sample)

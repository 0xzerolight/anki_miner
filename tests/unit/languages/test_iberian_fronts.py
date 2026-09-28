"""Iberian card fronts and input: the es/ca/pt soft-hyphen normalize."""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages._spaced.script import nbsp_shy_normalize
from anki_miner.languages.registry import get_profile

SHY = "\N{SOFT HYPHEN}"
NBSP = "\N{NO-BREAK SPACE}"

# --------------------------------------------------------------------------
# IBER-05: an e-book's soft hyphen never stays inside a word
# --------------------------------------------------------------------------


def test_the_shared_normalize_drops_soft_hyphens_and_no_break_spaces():
    assert nbsp_shy_normalize(f"compu{SHY}tador{NBSP}nou") == "computador nou"
    assert nbsp_shy_normalize(unicodedata.normalize("NFD", "canción")) == "canción"
    assert nbsp_shy_normalize(f"cafe{SHY}\N{COMBINING ACUTE ACCENT}") == "café"  # dropped first: the accent composes


@pytest.mark.parametrize(
    ("code", "text", "normalized"),
    [
        ("es", f"La pelí{SHY}cula empie{SHY}za.", "La película empieza."),
        ("ca", f"La pel·lí{SHY}cula comen{SHY}ça.", "La pel·lícula comença."),
        ("pt", f"O compu{SHY}tador{NBSP}novo", "O computador novo"),
    ],
)
def test_the_iberian_profiles_strip_a_soft_hyphen(code, text, normalized):
    assert get_profile(code).normalize(text) == normalized


def test_spanish_normalizes_with_the_shared_helper():
    assert get_profile("es").normalize is nbsp_shy_normalize

"""Swedish data for the shared substrate: the abbreviation set, the particle order, normalisation (B.2)."""

from __future__ import annotations

from spacy.lang.sv.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

from anki_miner.languages.sv.abbreviations import (
    SV_ABBREVIATION_ADDITIONS,
    SV_ABBREVIATION_DROPS,
    SV_ABBREVIATIONS,
)


def test_the_abbreviation_set_is_spacys_dotted_exceptions_minus_the_drops():
    seeded = {
        key[:-1].casefold() for key in TOKENIZER_EXCEPTIONS if key.endswith(".") and any(c.isalpha() for c in key)
    }
    assert (seeded - SV_ABBREVIATION_DROPS) | SV_ABBREVIATION_ADDITIONS == SV_ABBREVIATIONS
    assert len(SV_ABBREVIATIONS) == 81
    assert {"t.ex", "bl.a", "kl", "kr", "osv", "dvs", "m.m"} <= SV_ABBREVIATIONS
    assert not {"i", "m", "ung", "lat", "min", "max"} & SV_ABBREVIATIONS
    assert all(entry == entry.casefold() and not entry.endswith(".") for entry in SV_ABBREVIATIONS)

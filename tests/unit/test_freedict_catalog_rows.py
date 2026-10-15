"""DEF-05: the FreeDict rows the L8 research found (lt, sl, ar, hr, hu)."""

from __future__ import annotations

import re

import pytest

from anki_miner.languages.registry import get_profile

FREEDICT_ROWS = {
    "lt": ("freedict-lit-eng", "lit-eng", "0.7.2", "wty-lt-en"),
    "sl": ("freedict-slv-eng", "slv-eng", "0.1", "wty-sl-en"),
    "ar": ("freedict-ara-eng", "ara-eng", "0.6.3", "wty-ar-en"),
    "hr": ("freedict-hrv-eng", "hrv-eng", "0.1.2", "wty-sh-en"),
    "hu": ("freedict-hun-eng", "hun-eng", "0.4.1", "wty-hu-en"),
}


@pytest.mark.parametrize("code", sorted(FREEDICT_ROWS))
def test_each_row_is_a_pinned_sha256_checked_freedict_release(code: str) -> None:
    spec_id, name, version, _wty = FREEDICT_ROWS[code]
    (spec,) = [spec for spec in get_profile(code).catalog if spec.id == spec_id]

    assert spec.kind == "dict"
    assert spec.url == (
        f"https://download.freedict.org/dictionaries/{name}/{version}/freedict-{name}-{version}.stardict.tar.xz"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", spec.sha256)
    assert "GPL" in spec.license_note and "FreeDict" in spec.license_note


@pytest.mark.parametrize("code", sorted(FREEDICT_ROWS))
def test_freedict_is_listed_before_wiktionary_so_it_lands_below_it(code: str) -> None:
    """The download flow prepends each import, so the earlier row ends lower in the chain."""
    spec_id, _name, _version, wty = FREEDICT_ROWS[code]
    ids = [spec.id for spec in get_profile(code).catalog]

    assert ids.index(spec_id) < ids.index(wty)


def test_no_other_language_gained_a_freedict_row() -> None:
    from anki_miner.languages import AVAILABLE_LANGUAGES

    with_rows = {
        code
        for code in AVAILABLE_LANGUAGES
        if any(spec.id.startswith("freedict-") for spec in get_profile(code).catalog)
    }
    assert with_rows == set(FREEDICT_ROWS)

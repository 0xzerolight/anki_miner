"""The Hebrew form resolver's review rulings (HE-01..HE-07), over rows rendered the way the importer renders them.

Every Hebrew word is built from NAMED Unicode characters, so no pointed literal and no
right-to-left run is written into this file.
"""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages.he.morphology import vocalised_from_content

QAMATS = "\N{HEBREW POINT QAMATS}"
PATAH = "\N{HEBREW POINT PATAH}"


def _word(*letters: str) -> str:
    return "".join(unicodedata.lookup(f"HEBREW LETTER {name}") for name in letters)


def _pointed(word: str) -> str:
    """The word with a point on every letter: what a Grammar head or a gloss target carries."""
    return "".join(letter + QAMATS for letter in word)


ASA = _word("AYIN", "SHIN", "HE")


# --------------------------------------------------------------------------
# HE-05: the reading is one pointed spelling
# --------------------------------------------------------------------------

AKHSHAV = _word("AYIN", "KAF", "SHIN", "YOD", "VAV")
AKHSHAV_POINTED = _pointed(_word("AYIN", "KAF", "SHIN", "VAV"))


def _grammar(head: str) -> str:
    return f'<div class="gloss-sc-div" data-sc-content="Grammar-content">{head} \N{BULLET} (akhshav) m</div>'


@pytest.mark.parametrize(
    "head",
    [
        f"{AKHSHAV} / {AKHSHAV_POINTED}",
        f"{AKHSHAV_POINTED} / {AKHSHAV}",
        f"{AKHSHAV} or {AKHSHAV_POINTED}",
        f"{AKHSHAV} \\ {AKHSHAV_POINTED}",
        f"{AKHSHAV}, {AKHSHAV_POINTED}",
    ],
    ids=["slash", "pointed-first", "or", "backslash", "comma"],
)
def test_a_head_with_two_spellings_reads_as_the_pointed_one(head):
    assert vocalised_from_content(_grammar(head)) == AKHSHAV_POINTED


def test_two_pointed_spellings_read_as_the_first():
    other = "".join(letter + PATAH for letter in AKHSHAV)
    assert vocalised_from_content(_grammar(f"{AKHSHAV_POINTED} / {other}")) == AKHSHAV_POINTED


def test_an_unpointed_head_reads_as_its_first_spelling():
    assert vocalised_from_content(_grammar(f"{AKHSHAV} / {ASA}")) == AKHSHAV


def test_a_single_spelling_is_read_unchanged():
    assert vocalised_from_content(_grammar(AKHSHAV_POINTED)) == AKHSHAV_POINTED

"""Croatian and Slovenian: the Latin-2 ladder and „…“ quotes."""

from __future__ import annotations

import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.services.reading._util import decode_with_ladder
from anki_miner.services.reading.sentence_splitter import split_sentences

# --------------------------------------------------------------------------
# HRSL-03: Latin-2 before cp1250
# --------------------------------------------------------------------------

LATIN2_LINES = {
    "hr": "Žena je šutjela cijelu večer. Što želiš? Muškarac je pušio.",
    "sl": "Žena je šla v šolo. Kaj želiš? Moški je kadil na terasi.",
}


@pytest.mark.parametrize("code", ["hr", "sl"])
def test_a_latin2_file_decodes_as_latin2(code):
    profile = get_profile(code)
    raw = LATIN2_LINES[code].encode("iso8859_2")
    text, won = decode_with_ladder(
        raw, encodings=profile.import_encodings, script_check=profile.script.contains_target_script
    )
    assert (text, won) == (LATIN2_LINES[code], "iso8859_2")


@pytest.mark.parametrize("code", ["hr", "sl"])
def test_a_cp1250_file_still_decodes_as_cp1250(code):
    """Its š ž Š Ž „ “ – … are C1 controls under Latin-2, which the single-byte guard rejects."""
    profile = get_profile(code)
    line = f"„{LATIN2_LINES[code]}“ – rekla je…"
    text, won = decode_with_ladder(
        line.encode("cp1250"), encodings=profile.import_encodings, script_check=profile.script.contains_target_script
    )
    assert (text, won) == (line, "cp1250")


# --------------------------------------------------------------------------
# HRSL-07: „…“ and »…« hold a multi-sentence quote together
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("hr", "„Dobro jutro. Kako si?“ upitala je."),
        ("hr", "»Dobro jutro. Kako si?« upitala je."),
        ("hr", "„Ne znam. Pitaj njega.” rekao je Marko."),
        ("sl", "„Dobro jutro. Kako si?“ je vprašala."),
        ("sl", "»Ne vem. Vprašaj njega.« je rekel Marko."),
    ],
)
def test_a_quoted_pair_of_sentences_stays_one_sentence(code, text):
    assert split_sentences(text, rules=get_profile(code).sentence_rules) == [text]

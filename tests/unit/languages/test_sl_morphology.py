"""Slovenian data: the MULTEXT-East fine-tag table, the tone fold, the subtitle filter."""

from __future__ import annotations

import importlib
import json
import re
import unicodedata
from pathlib import Path

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, nfc_normalize
from anki_miner.languages.sl.morphology import (
    SL_ALLOWED_POS,
    SL_EXCLUDED_SUBTYPES,
    SL_MODEL_PACKAGE,
    SL_SUBTITLE_REGEX,
    sl_tone_fold,
)

#: The MULTEXT-East categories a Slovenian card front may come from (the hr rule, verbatim).
KEPT_CATEGORIES = ("A", "Nc", "R", "Vm")

#: Accented strings come from the committed fixture, never from a literal in this file: a combining
#: mark typed into source is exactly what the merge-time scan rejects, and the real dictionary mixes
#: precomposed letters with a raw combining mark (the schwa headword carries U+030F uncomposed).
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sl"
ROW = json.loads((FIXTURES / "wty_row.json").read_text(encoding="utf-8"))
_HEAD_RE = re.compile(r'"Grammar-content"\}, "content": ("(?:[^"\\]|\\.)*")')


def _model_labels() -> list[str]:
    model = importlib.import_module(SL_MODEL_PACKAGE).load(exclude=["ner", "senter", "parser"])
    return list(model.meta["labels"]["tagger"])


def _head_lines() -> dict[str, str]:
    heads = {}
    for row in ROW["term_rows"]:
        match = _HEAD_RE.search(json.dumps(row, ensure_ascii=False))
        if match is not None:
            heads[row[0]] = json.loads(match.group(1))
    return heads


def _marks(text: str) -> list[str]:
    """Every combining mark left standing, except the caron, which is part of a Slovenian letter."""
    return [c for c in unicodedata.normalize("NFD", text) if 0x300 <= ord(c) <= 0x36F and c != "\N{COMBINING CARON}"]


def test_the_table_is_re_derived_from_the_installed_model():
    """Generated from meta.json, never transcribed: every label outside {A, Nc, R, Vm}."""
    labels = _model_labels()
    assert len(labels) == 1141
    expected = [lab for lab in labels if not (lab[:1] in KEPT_CATEGORIES or lab[:2] in KEPT_CATEGORIES)]
    assert sorted(SL_EXCLUDED_SUBTYPES) == sorted(expected)
    assert len(SL_EXCLUDED_SUBTYPES) == 784


def test_the_kept_labels_are_the_vocabulary_categories():
    kept = set(_model_labels()) - set(SL_EXCLUDED_SUBTYPES)
    assert len(kept) == 357
    assert all(lab[:1] in KEPT_CATEGORIES or lab[:2] in KEPT_CATEGORIES for lab in kept)
    assert {"Ncfpn", "Ncfdn", "Vmep-sm", "Vmpp-dm", "Rgp", "Agpfsa"} <= kept


def test_the_non_vocabulary_tags_are_excluded():
    """Copulas, numerals, proper nouns, demonstratives, particles and residuals reach an allowed UPOS."""
    for label in ("Va-r3p-n", "Va-r3s-n", "Mlcmda", "Mdo", "Npmsn", "Pd-nsn", "Q", "Y", "Z", "_SP"):
        assert label in SL_EXCLUDED_SUBTYPES, label


def test_allowed_pos_is_the_shared_upos_set():
    assert SL_ALLOWED_POS == UPOS_ALLOWED


def test_the_tone_fold_recovers_the_orthographic_form():
    """wty head lines are written in an accent notation Slovenian orthography never uses."""
    head = _head_lines()["brati"]
    headword, rest = head.split(" ", 1)
    assert headword != "brati" and sl_tone_fold(headword) == "brati"
    partner = rest.split("perfective ", 1)[1].rstrip(")")
    assert sl_tone_fold(partner) == "prebrati or prebirati"
    assert not _marks(sl_tone_fold(head))


def test_the_caron_is_a_letter_and_is_never_folded():
    for word in ("čitati", "šola", "žival", "ločiti"):
        assert sl_tone_fold(word) == word


def test_the_schwa_headword_is_left_exactly_as_written():
    """The schwa is a dictionary-notation LETTER, not a mark, and it is not a fold base.

    So the ``pes`` head line comes back unchanged: the schwa stays, and the double grave ON the
    schwa stays with it. Both are deliberate. 0 of the 21 aspect partners in wty-sl-en carries a
    schwa or a stroked l, and the fold only ever prints a partner, so widening the base set would be
    a mechanism with no caller - and no mark fold could reach ``pes`` anyway. The gender read is
    unaffected: D2's shared ``_without_combining_marks`` strips every mark before the hook looks for
    the gender letter, which ``test_sl_wty_row.py`` pins on this same row.
    """
    headword = _head_lines()["pes"].split(" ", 1)[0]
    assert sl_tone_fold(headword) == headword
    assert "\N{LATIN SMALL LETTER SCHWA}" in headword and _marks(headword)


def test_the_subtitle_filter_strips_a_slovene_speaker_label():
    """MUSIC_PATTERN strips the note CHARACTERS, not the lyric between them (lt's shape), so the
    surviving words are compared after collapsing whitespace."""
    for cue, kept in (
        ("ŽENSKA: Pozdravljeni.", "Pozdravljeni."),
        ("[vrata se zaprejo] Pozdravljeni.", "Pozdravljeni."),
        ("(smeh) Pozdravljeni.", "Pozdravljeni."),
        ("- Pozdravljeni. - Dober dan.", "Pozdravljeni. Dober dan."),
        ("♪ Pesem ♪ Pozdravljeni.", "Pesem Pozdravljeni."),
    ):
        assert " ".join(re.sub(SL_SUBTITLE_REGEX, "", cue).split()) == kept


def test_the_latin_default_misses_the_slovene_speaker_label():
    assert re.sub(LATIN_SUBTITLE_REGEX, "", "ŽENSKA: Pozdravljeni.") == "ŽENSKA: Pozdravljeni."


def test_normalize_is_the_shared_nfc():
    assert nfc_normalize("c\N{COMBINING CARON}itati") == "čitati"

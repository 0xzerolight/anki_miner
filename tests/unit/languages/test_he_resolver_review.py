"""The Hebrew form resolver's review rulings (HE-01..HE-07), over rows rendered the way the importer renders them.

Each case is a small entry set in the ``wty-he-en`` shapes: a lemma row is structured content with
a Grammar head, an optional Etymology block and a ``glosses`` list; a form row is a deinflection
pair. Both go through the importer's own ``render_glossary_entry``, so the resolver reads exactly
the HTML a real index stores. The committed real-row subset is exercised in
``test_he_dictionary.py``; these cases are the shapes that subset does not carry.

Every Hebrew word is built from NAMED Unicode characters, so no pointed literal and no
right-to-left run is written into this file.
"""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages.he.morphology import HebrewLemmaPass, vocalised_from_content
from anki_miner.languages.he.tokenizer import to_duck_tokens
from anki_miner.services.dictionary.importers.yomitan_importer import render_glossary_entry

QAMATS = "\N{HEBREW POINT QAMATS}"
PATAH = "\N{HEBREW POINT PATAH}"


def _word(*letters: str) -> str:
    return "".join(unicodedata.lookup(f"HEBREW LETTER {name}") for name in letters)


def _pointed(word: str) -> str:
    """The word with a point on every letter: what a Grammar head or a gloss target carries."""
    return "".join(letter + QAMATS for letter in word)


def _lemma(tags: str, *glosses: str, head: str = "", etymology: str = "", gloss_tag: str = "") -> tuple[str, str]:
    """A lemma row as ``(content, tags)``, rendered from the wty structured-content shape."""
    preamble: list[dict] = []
    if head:
        preamble.append(
            {
                "tag": "details",
                "data": {"content": "details-entry-Grammar"},
                "content": [
                    {"tag": "summary", "data": {"content": "summary-entry"}, "content": "Grammar"},
                    {"tag": "div", "data": {"content": "Grammar-content"}, "content": f"{head} \N{BULLET} (x)"},
                ],
            }
        )
    if etymology:
        preamble.append(
            {
                "tag": "details",
                "data": {"content": "details-entry-Etymology"},
                "content": [
                    {"tag": "summary", "data": {"content": "summary-entry"}, "content": "Etymology"},
                    {"tag": "div", "data": {"content": "Etymology-content"}, "content": etymology},
                ],
            }
        )
    chips = (
        [
            {
                "tag": "div",
                "data": {"content": "tags"},
                "content": [
                    {"tag": "span", "data": {"content": "tag", "category": "partOfSpeech"}, "content": gloss_tag}
                ],
            }
        ]
        if gloss_tag
        else []
    )
    items = [{"tag": "li", "content": [{"tag": "div", "content": [*chips, gloss]}]} for gloss in glosses]
    content: list[dict] = [{"tag": "ol", "data": {"content": "glosses"}, "content": items}]
    if preamble:
        content.insert(
            0, {"tag": "div", "content": [{"tag": "div", "data": {"content": "preamble"}, "content": preamble}]}
        )
    glossary = [{"type": "structured-content", "content": content}]
    return (
        render_glossary_entry(glossary, definition_tags=tags.split(), dict_id="wty-he-en", media_collector=None),
        tags,
    )


def _form(target: str) -> tuple[str, str]:
    """A ``non-lemma`` row naming *target*: a deinflection pair, as wty stores it."""
    content = render_glossary_entry(
        [[target, ["form"]]], definition_tags=["non-lemma"], dict_id="wty-he-en", media_collector=None
    )
    return content, "non-lemma"


def _resolve(entries: dict[str, list[tuple[str, str]]], surface: str) -> tuple[str, str, str, str]:
    """``(front, pos1, pos2, vocalised)`` a one-word line mines to over *entries*."""

    def forms(terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        return {term: entries[term] for term in terms if term in entries}

    [token] = HebrewLemmaPass()(to_duck_tokens(surface), None, forms)
    return token.feature.lemma, token.feature.pos1, token.feature.pos2, token.feature.vocalised


LAASOT = _word("LAMED", "AYIN", "SHIN", "VAV", "TAV")
ASA = _word("AYIN", "SHIN", "HE")


# --------------------------------------------------------------------------
# HE-03: the function-word tier applies to the resolved front
# --------------------------------------------------------------------------

SHELO = _word("SHIN", "LAMED", "ALEF")
LO = _word("LAMED", "ALEF")


def test_a_proclitic_stopword_is_marked_like_the_bare_one():
    """she-lo resolves to lo, which the tier lists, whatever part of speech wty gives lo."""
    entries = {LO: [_lemma("adv", "not")]}
    assert _resolve(entries, SHELO)[:3] == (LO, "ADV", "stopword")


def test_a_resolved_content_word_is_not_marked():
    entries = {LAASOT: [_lemma("v", f"to-infinitive of {_pointed(ASA)}.")], ASA: [_lemma("v", "to do")]}
    assert _resolve(entries, LAASOT)[2] == ""


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

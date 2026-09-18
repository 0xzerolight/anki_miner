"""Danish books and cues: S8 abbreviations and day numbers, Danish dialogue quotes, soft hyphens (S5), EPUB wraps."""

from __future__ import annotations

import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.reading.epub_source import _line_join, _parse_content, _walk_body
from anki_miner.services.reading.sentence_splitter import split_sentences

RULES = get_profile("da").sentence_rules


@pytest.fixture(scope="module")
def parser():
    return get_profile("da").create_parser(switch_language(AnkiMinerConfig(), "da"))


def _fronts(parser, text: str, **kwargs) -> tuple[set[str], list]:
    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], False, **kwargs
    )
    return {word.mined_form for word in words}, words


def test_danish_abbreviations_keep_a_sentence_whole():
    assert split_sentences("Mødet er kl. 9, dvs. om en time. Kom til tiden.", rules=RULES) == [
        "Mødet er kl. 9, dvs. om en time.",
        "Kom til tiden.",
    ]
    assert split_sentences("Han taler bl.a. tysk. Hun taler fransk.", rules=RULES) == [
        "Han taler bl.a. tysk.",
        "Hun taler fransk.",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Klokken er ti. Vi går nu.",
        "Det er min. Ikke din.",
        "God jul. Godt nytår.",
        "Han så en ræv. Den løb væk.",
    ],
)
def test_an_ordinary_word_still_ends_its_sentence(text):
    assert len(split_sentences(text, rules=RULES)) == 2


@pytest.mark.parametrize(
    "text",
    [
        "Det ved man. Ingen tvivl.",
        "Hun så det på tv. Bagefter sov hun.",
        "Det blæser i vind. Nu regner det.",
    ],
)
def test_a_dropped_spacy_exception_ends_its_sentence(text):
    """The DA_ABBREVIATION_DROPS half: man, tv and vind are Danish words, not abbreviations."""
    assert len(split_sentences(text, rules=RULES)) == 2


def test_a_danish_date_ordinal_does_not_split_its_sentence():
    """DA8 forward direction: day numbers 1-31 are abbreviation keys, so an ordinal dot continues."""
    assert split_sentences("Han kom d. 3. marts og blev en uge.", rules=RULES) == [
        "Han kom d. 3. marts og blev en uge."
    ]
    assert split_sentences("Hun fik 1. plads ved stævnet.", rules=RULES) == ["Hun fik 1. plads ved stævnet."]
    assert split_sentences("Det kostede 3 mia. kr. i alt.", rules=RULES) == ["Det kostede 3 mia. kr. i alt."]


def test_a_sentence_ending_in_a_house_number_runs_on():
    """DA8's accepted known limit (judge finding 7): the price of the day-number keys.

    ``_period_continues`` cannot tell an ordinal from a sentence-final cardinal, so a bare 1-31 before a full stop
    never terminates. Traded knowingly: the ordinal date is pervasive in Danish prose, this form is rare.
    """
    assert split_sentences("Han bor i Nørregade 28. Bygningen er gammel.", rules=RULES) == [
        "Han bor i Nørregade 28. Bygningen er gammel."
    ]
    # A number outside 1-31 is not a key, so it splits as it should.
    assert len(split_sentences("Han bor i Nørregade 88. Bygningen er gammel.", rules=RULES)) == 2


def test_danish_quotes_hold_their_sentences():
    text = "»Hvor skal du hen? Nu?« spurgte han. Så gik han."
    assert split_sentences(text, rules=RULES) == ["»Hvor skal du hen? Nu?« spurgte han.", "Så gik han."]
    without = dataclasses.replace(RULES, openers=RULES.openers - {"»"}, closers=RULES.closers - {"«"})
    assert len(split_sentences(text, rules=without)) == 3


def test_the_low_quote_pair_holds_too():
    text = "„Hvem er der? Hallo?“ råbte hun. Ingen svarede."
    assert split_sentences(text, rules=RULES) == ["„Hvem er der? Hallo?“ råbte hun.", "Ingen svarede."]


def test_a_soft_hyphen_never_splits_a_mined_word(parser):
    fronts, words = _fronts(parser, "Det er ge\N{SOFT HYPHEN}nialt, sagde hun.")
    # genialt, not genial: the model keeps the neuter adjective form as its lemma. The point is the join.
    assert "genialt" in fronts
    assert all("\N{SOFT HYPHEN}" not in word.sentence for word in words)


def test_the_unspaced_speaker_dash_is_stripped_so_the_cue_initial_word_mines(parser):
    fronts, words = _fronts(parser, "-Bogen ligger her. -Nej, jeg vil sove.", subtitle_cleanup=True)
    assert {"bog", "sove"} <= fronts
    assert all(not word.sentence.startswith("-") for word in words)


def test_the_latin_default_would_lose_the_word():
    """Negative control (DA21): spaCy glues -Bogen into one PUNCT token, and the Latin dash rule needs a space."""
    config = dataclasses.replace(switch_language(AnkiMinerConfig(), "da"), subtitle_regex_filter=LATIN_SUBTITLE_REGEX)
    parser = get_profile("da").create_parser(config)
    fronts, _words = _fronts(parser, "-Bogen ligger her. -Nej, jeg vil sove.", subtitle_cleanup=True)
    assert "bog" not in fronts and "sove" in fronts


def test_epub_line_wraps_join_with_a_space_for_danish():
    assert _line_join(RULES) == " "
    body, _is_cover = _parse_content(
        b'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b"<p>Jeg leder efter\n      huset.</p></body></html>"
    )
    paragraphs, _gaiji = _walk_body(body, line_join=_line_join(RULES))
    assert paragraphs == ["Jeg leder efter huset."]

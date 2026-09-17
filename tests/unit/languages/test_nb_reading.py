"""Norwegian books and cues: S8 abbreviations, «…» dialogue, soft hyphens (S5), the unspaced dash (E.4), EPUB wraps."""

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

RULES = get_profile("nb").sentence_rules


@pytest.fixture(scope="module")
def parser():
    return get_profile("nb").create_parser(switch_language(AnkiMinerConfig(), "nb"))


def _fronts(parser, text: str, **kwargs) -> tuple[set[str], list]:
    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], False, **kwargs
    )
    return {word.mined_form for word in words}, words


def test_norwegian_abbreviations_keep_a_sentence_whole():
    assert split_sentences("Møtet er kl. 9, dvs. om en time. Kom i tide.", rules=RULES) == [
        "Møtet er kl. 9, dvs. om en time.",
        "Kom i tide.",
    ]
    assert split_sentences("Han snakker f.eks. tysk. Hun snakker fransk.", rules=RULES) == [
        "Han snakker f.eks. tysk.",
        "Hun snakker fransk.",
    ]


@pytest.mark.parametrize("text", ["Klokka er ti. Vi går nå.", "Det er min. Ikke din.", "God jul. Godt nytt år."])
def test_an_ordinary_word_still_ends_its_sentence(text):
    assert len(split_sentences(text, rules=RULES)) == 2


def test_guillemets_hold_their_sentences():
    text = "«Hvor skal du? Nå?» spurte han. Så gikk han."
    assert split_sentences(text, rules=RULES) == ["«Hvor skal du? Nå?» spurte han.", "Så gikk han."]
    without = dataclasses.replace(RULES, openers=RULES.openers - {"«"}, closers=RULES.closers - {"»"})
    assert len(split_sentences(text, rules=without)) == 3


def test_a_soft_hyphen_never_splits_a_mined_word(parser):
    fronts, words = _fronts(parser, "Det er ge\N{SOFT HYPHEN}nialt, sa hun.")
    assert "genial" in fronts
    assert all("\N{SOFT HYPHEN}" not in word.sentence for word in words)


def test_the_unspaced_speaker_dash_is_stripped_so_the_cue_initial_verb_mines(parser):
    fronts, words = _fronts(parser, "-Kom hit. -Nei, jeg vil sove.", subtitle_cleanup=True)
    assert {"komme", "hit", "sove"} <= fronts
    assert all(not word.sentence.startswith("-") for word in words)


def test_the_latin_default_would_lose_the_verb():
    """Negative control: spaCy glues -Kom into one PUNCT token, and the Latin dash rule needs a space."""
    config = dataclasses.replace(switch_language(AnkiMinerConfig(), "nb"), subtitle_regex_filter=LATIN_SUBTITLE_REGEX)
    parser = get_profile("nb").create_parser(config)
    fronts, _words = _fronts(parser, "-Kom hit. -Nei, jeg vil sove.", subtitle_cleanup=True)
    assert "komme" not in fronts and "sove" in fronts


def test_epub_line_wraps_join_with_a_space_for_norwegian():
    assert _line_join(RULES) == " "
    body, _is_cover = _parse_content(
        b'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b"<p>Jeg leter etter\n      huset.</p></body></html>"
    )
    paragraphs, _gaiji = _walk_body(body, line_join=_line_join(RULES))
    assert paragraphs == ["Jeg leter etter huset."]

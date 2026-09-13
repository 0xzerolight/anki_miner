"""Noun gender for Portuguese cards, read from REAL rendered wty-pt-en rows (§4.8, D12)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages.pt.morphology import PT_GENDER_LABELS
from anki_miner.languages.registry import get_profile

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "pt" / "wty_row.json").read_text(encoding="utf-8"))
CONFIG = AnkiMinerConfig()


def _word(term: str, morph: str = "", pos: str = "NOUN") -> SimpleNamespace:
    return SimpleNamespace(pos=pos, morph=morph, definition_html=FIXTURE["entries"][term]["definition_html"])


def _hook() -> GrammarTagHook:
    (hook,) = [hook for hook in get_profile("pt").render_hooks if isinstance(hook, GrammarTagHook)]
    return hook


def test_the_fixture_carries_its_licence_and_the_raw_tags():
    assert "CC BY-SA 4.0" in FIXTURE["_attribution"]
    assert FIXTURE["entries"]["livro"]["term_bank_rows"][0][2] == "n masc"
    assert FIXTURE["entries"]["estudante"]["term_bank_rows"][0][2] == "n fem masc"


@pytest.mark.parametrize(
    ("term", "morph", "article"),
    [
        ("livro", "", "o"),
        ("cidade", "", "a"),
        ("problema", "", "o"),  # -a noun, masculine by the dictionary
        ("foto", "", "a"),  # -o noun, feminine by the dictionary
        ("guarda-chuva", "", "o"),
        ("estudante", "", None),  # chips fem+masc, head line "m or f by sense": nothing to print
        ("estudante", "Gender=Fem", "a"),  # morph wins: the chips (fem masc) include it; the model read the article
        ("mapa", "", None),  # "m or (obsolete) f"
        ("mapa", "Gender=Masc", "o"),
        ("livro", "Gender=Fem", "o"),  # en contract item 9: morph yields to chips that exclude it (livro: masc only)
    ],
)
def test_gender_from_the_real_rendered_rows(term, morph, article):
    expected = {} if article is None else {"noun_gender": article}
    assert _hook().render(_word(term, morph), config=CONFIG) == expected


def test_a_form_of_row_is_not_a_noun():
    assert _hook().render(_word("dá", pos="VERB"), config=CONFIG) == {}


def test_the_profile_hook_is_the_labelled_gender_hook():
    reference = GrammarTagHook(("noun_gender",), gender_labels=PT_GENDER_LABELS)
    for term in FIXTURE["entries"]:
        assert _hook().render(_word(term), config=CONFIG) == reference.render(_word(term), config=CONFIG)

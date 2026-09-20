"""The Indonesian card hooks (spec C.5, plan D10): Root and Affixes from the etymology line, Formal form.

The etymology block is the shape the Yomitan importer renders for wty-id-en (copied from a rendered
``merugikan`` entry); the real rows go through the importer in ``test_id_parser.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.id.render import FormalFormHook, RootAffixHook, etymology_parse


def _html(etymology: str) -> str:
    return (
        '<details class="gloss-sc-details" data-sc-content="details-entry-Etymology">'
        '<summary class="gloss-sc-summary" data-sc-content="summary-entry">Etymology</summary>'
        f'<div class="gloss-sc-div" data-sc-content="Etymology-content">{etymology}</div></details>'
        '<ol class="gloss-sc-ol" data-sc-content="glosses"><li class="gloss-sc-li">'
        '<div class="gloss-sc-div">to harm</div></li></ol>'
    )


@pytest.mark.parametrize(
    ("etymology", "parsed"),
    [
        ("meng- + rugi + -kan", ("rugi", "meng- + rugi + -kan")),
        ("From meng- + beli.", ("beli", "meng- + beli")),
        ("From ke- + adab + -an.", ("adab", "ke- + adab + -an")),
        ("Equivalent to ber- + jalan.", ("jalan", "ber- + jalan")),
        ("From ke- -an (confix) + tahu (know).", ("tahu", "ke- + tahu + -an")),
        ("beli + -kan", ("beli", "beli + -kan")),
        ("Borrowed from Dutch boek.", None),  # no affix formula
        ("From Malay rumah, from Proto-Malayic *rumah.", None),
        ("Compound of rumah + sakit.", None),  # two bare words: a compound, not a root
    ],
)
def test_the_etymology_formula(etymology, parsed):
    assert etymology_parse(_html(etymology)) == parsed


def test_the_first_formula_wins_and_no_block_is_no_parse():
    assert etymology_parse(_html("From meng- + beli.") + _html("From ber- + jalan.")) == ("beli", "meng- + beli")
    assert etymology_parse("<div>to buy</div>") is None and etymology_parse("") is None


def test_the_root_hook_fills_both_fields_or_neither():
    hook = RootAffixHook()
    assert hook.field_names() == ("root", "affixes")
    word = SimpleNamespace(definition_html=_html("From meng- + beli."), mined_form="membeli")
    assert hook.render(word, config=AnkiMinerConfig()) == {"root": "beli", "affixes": "meng- + beli"}
    assert hook.render(SimpleNamespace(definition_html="", mined_form="beli"), config=AnkiMinerConfig()) == {}


def test_the_formal_hook_names_the_formal_spelling_of_a_colloquial_front():
    hook = FormalFormHook()
    assert hook.field_names() == ("formal_form",)

    def render(front: str) -> dict[str, str]:
        return hook.render(SimpleNamespace(mined_form=front), config=AnkiMinerConfig())

    assert render("nggak") == {"formal_form": "tidak"}
    assert render("beliin") == {"formal_form": "membelikan"}
    assert render("rumah") == {} and render("") == {}

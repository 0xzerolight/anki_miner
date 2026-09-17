"""Addendum A (Ruling S1): a trailing masculine animacy qualifier on the wty head line, and opt-in animacy labels."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook

CONFIG = AnkiMinerConfig()
FIXTURES = Path(__file__).parents[2] / "fixtures"
SHIPPED = ("en", "ca", "de", "pt", "fr", "es", "it", "nl")
PL_GENDERS = {"masc": "m", "fem": "f", "neut": "n"}
PL_ANIMACY = {"pers": "m pers", "anim": "m anim", "inan": "m inan"}
_HEAD_RE = re.compile(r'data-sc-content="Grammar-content"[^>]*>(.*?)</div>', re.S)


def html(head: str) -> str:
    return (
        '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="wty" data-dictionary-id="wty">'
        '<i>(wty)</i><ul class="gloss-list" data-count="1"><li class="gloss-item"><div class="gloss-content">'
        '<div class="gloss-sc-div" data-sc-content="preamble"><details class="gloss-sc-details" '
        'data-sc-content="details-entry-Grammar"><summary class="gloss-sc-summary" data-sc-content="summary-entry">'
        f'Grammar</summary><div class="gloss-sc-div" data-sc-content="Grammar-content">{head}</div></details></div>'
        "</div></li></ul></li></ol></div>"
    )


def noun(head: str, morph: str = "") -> SimpleNamespace:
    return SimpleNamespace(pos="NOUN", morph=morph, definition_html=html(head), mined_form="")


def render(w, fields=("noun_gender",), **kwargs) -> dict[str, str]:
    return GrammarTagHook(fields, **kwargs).render(w, config=CONFIG)


@pytest.mark.parametrize(
    "head",
    [
        "stół m inan (diminutive stolik)",
        "student m pers (female equivalent studentka)",
        "pies m animal (diminutive psiak)",
        "pə\u030fs m anim (female equivalent psíca)",  # a double grave on the schwa (D2 pre-fold)
    ],
)
def test_one_trailing_animacy_qualifier_no_longer_hides_the_gender(head):
    assert render(noun(head)) == {"noun_gender": "masculine"}


def test_only_one_qualifier_is_skipped_and_a_headword_named_like_one_is_kept():
    assert render(noun("stół m inan anim")) == {}
    assert render(noun("anim")) == {}
    assert render(noun("inan m")) == {"noun_gender": "masculine"}


def test_an_or_chain_with_a_trailing_qualifier_still_shares_its_article():
    hook = GrammarTagHook(("noun_article",), article_map={"masc": "de", "fem": "de", "neut": "het"})
    assert hook.render(noun("koffie f or m inan (plural koffies)"), config=CONFIG) == {"noun_article": "de"}


@pytest.mark.parametrize(
    ("head", "morph", "expected"),
    [
        ("stół m inan (diminutive stolik)", "", "m inan"),
        ("pies m animal (diminutive psiak)", "", "m anim"),
        ("student m pers", "Animacy=Nhum", "m pers"),  # the head line wins over morph
        ("chłopak m", "Animacy=Hum|Case=Nom|Gender=Masc", "m pers"),
        ("koń m", "Animacy=Nhum", "m anim"),
        ("dom m", "Animacy=Inan", "m inan"),
        ("chłopak m", "", "m"),  # no qualifier anywhere: the plain label
        ("książka f", "Animacy=Inan", "f"),  # masculine only
        ("okno n", "", "n"),
    ],
)
def test_animacy_labels_name_the_masculine_subgender(head, morph, expected):
    out = render(noun(head, morph), gender_labels=PL_GENDERS, animacy_labels=PL_ANIMACY)
    assert out == {"noun_gender": expected}


def test_without_animacy_labels_the_label_is_todays():
    assert render(noun("stół m inan", "Animacy=Inan"), gender_labels=PL_GENDERS) == {"noun_gender": "m"}


def _shipped_head_lines(code: str) -> list[str]:
    data = json.loads((FIXTURES / code / "wty_row.json").read_text(encoding="utf-8"))
    stack: list[object] = [data]
    heads: list[str] = []
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str) and "yomitan-glossary" in node:
            heads.extend(_HEAD_RE.findall(node))
    return heads


@pytest.mark.parametrize("code", SHIPPED)
def test_no_shipped_head_line_ends_in_a_qualifier(code):
    """The skip is inert for every shipped language: none of their committed real rows carries a qualifier."""
    heads = _shipped_head_lines(code)
    assert heads
    for head in heads:
        tokens = re.sub(r"<[^>]+>", "", head).split("(", 1)[0].split()
        assert tokens[-1:] not in (["pers"], ["anim"], ["animal"], ["inan"]), (code, head)

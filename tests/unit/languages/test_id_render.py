"""The Indonesian card hooks (spec C.5, plan D10): Root and Affixes from the etymology line, Formal form.

The etymology block is the shape the Yomitan importer renders for wty-id-en (copied from a rendered
``merugikan`` entry); the real rows go through the importer in ``test_id_parser.py``.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.id.render import FormalFormHook, RootAffixHook, etymology_parse
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models import CardPayload, MediaData
from anki_miner.services.anki_note_builder import build_note


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


@pytest.mark.parametrize(
    ("etymology", "parsed"),
    [
        # kopi: the Latin formula of a borrowing chain is not the Indonesian word's root
        (
            "From Dutch kopie, from French copie, from Medieval Latin copia (“reproduction, transcript”), from Latin "
            "cōpia (“plenty, abundance”), from *coopia, from co- (“together”) + ops (“wealth, riches”).",
            None,
        ),
        # kapal: an English sense note after the Tamil step
        (
            "From Malay kapal (“ship”), from Classical Malay کاڤل (kapal), from Tamil கப்பல் (kappal, “ship”). In the "
            "third sense, a mistranslation of English ship (“relationship”), from relation + -ship.",
            None,
        ),
        ("From Old Javanese kagöm (“taken by fright”), from ka- + göm.", None),
        ("From ka- + göm.", None),  # a component never stops short of a letter
        # the Malay steps are the word's own history
        ("From Malay pilihan, from pilih + -an.", ("pilih", "pilih + -an")),
        ("Inherited from Malay perempuan, from per- -an + empu.", ("empu", "per- + empu + -an")),
        # an explicit marker still counts after a foreign step
        ("From English neophobia, from neo- + phobia. Equivalent to neo- + fobia.", ("fobia", "neo- + fobia")),
        (
            "From Malay lelaki, from Proto-Malayo-Polynesian (compare Madurese lalake). Partial reduplication of laki "
            "or infixed from laki + -el-.",
            ("laki", "laki + -el-"),
        ),
    ],
    ids=["kopi", "kapal", "kagum", "letter-guard", "pilihan", "perempuan", "neofobia", "lelaki"],
)
def test_a_bare_formula_after_a_foreign_step_is_not_the_root(etymology, parsed):
    assert etymology_parse(_html(etymology)) == parsed


def test_the_first_formula_wins_and_no_block_is_no_parse():
    assert etymology_parse(_html("From meng- + beli.") + _html("From ber- + jalan.")) == ("beli", "meng- + beli")
    assert etymology_parse("<div>to buy</div>") is None and etymology_parse("") is None


def _nowrap(*parts: str) -> str:
    return " + ".join(f'<span style="white-space:nowrap">{part}</span>' for part in parts)


def test_the_root_hook_fills_both_fields_or_neither():
    hook = RootAffixHook()
    assert hook.field_names() == ("root", "affixes")
    word = SimpleNamespace(definition_html=_html("From meng- + beli."), mined_form="membeli")
    assert hook.render(word, config=AnkiMinerConfig()) == {
        "root": "beli",
        "affixes": '<span style="white-space:nowrap">meng-</span> + <span style="white-space:nowrap">beli</span>',
    }
    assert hook.render(SimpleNamespace(definition_html="", mined_form="beli"), config=AnkiMinerConfig()) == {}


@pytest.mark.parametrize(
    ("etymology", "front", "fields"),
    [
        ("Equivalent to ber- + ajar.", "belajar", {"root": "ajar", "affixes": _nowrap("ber-", "ajar")}),  # allomorph
        ("From meng- + erti.", "ngerti", {"root": "erti", "affixes": _nowrap("meng-", "erti")}),  # formal: mengerti
        ("From meng- + beli.", "dibeli", {}),  # the entry its form row names
        ("From meng- + beli.", "kubeli", {}),  # the entry the ladder reaches
    ],
)
def test_the_root_hook_takes_a_prefix_only_the_front_opens_with(etymology, front, fields):
    word = SimpleNamespace(definition_html=_html(etymology), mined_form=front)
    assert RootAffixHook().render(word, config=AnkiMinerConfig()) == fields


def test_each_affix_is_escaped_inside_its_span(monkeypatch):
    monkeypatch.setattr("anki_miner.languages.id.render.etymology_parse", lambda _html: ("a<b", "a<b + -&an"))
    word = SimpleNamespace(definition_html="", mined_form="a<ban")
    assert RootAffixHook().render(word, config=AnkiMinerConfig())["affixes"] == _nowrap("a&lt;b", "-&amp;an")


def test_build_note_sends_the_affixes_markup_unescaped(make_tokenized_word):
    profile = get_profile("id")
    config = replace(
        switch_language(AnkiMinerConfig(), "id"),
        anki_fields={**profile.card_field_defaults, "word": "Expression", "affixes": "Affixes"},
    )
    word = make_tokenized_word(surface="membeli", lemma="membeli", reading="")
    affixes = _nowrap("meng-", "beli")
    payload = CardPayload(word=word, media=MediaData(), definition="to buy", extra_fields={"affixes": affixes})
    note = build_note(
        payload,
        config,
        set(),
        extra_optional_keys=frozenset(spec.key for spec in profile.extra_card_fields),
        extra_raw_html_keys=frozenset(spec.key for spec in profile.extra_card_fields if spec.raw_html),
    ).note
    assert note["fields"]["Affixes"] == affixes


def test_the_formal_hook_names_the_formal_spelling_of_a_colloquial_front():
    hook = FormalFormHook()
    assert hook.field_names() == ("formal_form",)

    def render(front: str) -> dict[str, str]:
        return hook.render(SimpleNamespace(mined_form=front), config=AnkiMinerConfig())

    assert render("nggak") == {"formal_form": "tidak"}
    assert render("beliin") == {"formal_form": "membelikan"}
    assert render("rumah") == {} and render("") == {}

"""GrammarTagHook: gender, article and plural from morph, tag chips and the wty head line (D8)."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import DEFAULT_GENDER_LABELS, GRAMMAR_FIELDS, GrammarTagHook

CONFIG = AnkiMinerConfig()
ARTICLES = {"masc": "der", "fem": "die", "neut": "das"}


def chip(category: str, name: str) -> str:
    return f'<span class="gloss-tag" data-category="{category}" title="{name}">{name}</span>'


def block(chips: str, head: str, dictionary: str = "wty-de-en") -> str:
    return (
        f'<li data-dictionary="{dictionary}" data-dictionary-id="{dictionary}">{chips}<i>({dictionary})</i>'
        '<ul class="gloss-list" data-count="1"><li class="gloss-item"><div class="gloss-content">'
        '<div class="gloss-sc-div" data-sc-content="preamble"><details class="gloss-sc-details" '
        'data-sc-content="details-entry-Grammar"><summary class="gloss-sc-summary" '
        f'data-sc-content="summary-entry">Grammar</summary><div class="gloss-sc-div" data-sc-content="Grammar-content">{head}'
        "</div></details></div></div></li></ul></li>"
    )


def html(*blocks: str) -> str:
    return f'<div class="yomitan-glossary"><ol data-count="1">{"".join(blocks)}</ol></div>'


FUCHS = html(
    block(
        chip("partOfSpeech", "n") + chip("gender-feminine", "fem") + chip("gender-masculine", "masc"),
        "Fuchs m (strong, genitive Fuchses, plural Füchse, diminutive Füchslein n or Füchschen n)",
    )
)


def word(definition_html: str = "", morph: str = "", pos: str = "NOUN", mined_form: str = "") -> SimpleNamespace:
    return SimpleNamespace(pos=pos, morph=morph, definition_html=definition_html, mined_form=mined_form)


def render(fields, w, **kwargs):
    return GrammarTagHook(fields, **kwargs).render(w, config=CONFIG)


def test_the_field_vocabulary_and_labels():
    assert GRAMMAR_FIELDS == ("noun_gender", "noun_article", "noun_plural", "aspect_pair")
    assert set(DEFAULT_GENDER_LABELS) == {"masc", "fem", "neut", "common"}
    hook = GrammarTagHook(("noun_gender", "noun_plural"))
    assert hook.field_names() == ("noun_gender", "noun_plural")
    assert inspect.signature(hook.render).parameters["config"].kind is inspect.Parameter.KEYWORD_ONLY


def test_morph_leads_when_the_chips_allow_it():
    out = render(("noun_gender", "noun_article"), word(FUCHS, "Case=Nom|Gender=Masc|Number=Sing"), article_map=ARTICLES)
    assert out == {"noun_gender": "masculine", "noun_article": "der"}
    see = html(
        block(
            chip("gender-feminine", "fem") + chip("gender-masculine", "masc") + chip("gender-neuter", "neut"),
            "See m (mixed, genitive Sees, plural Seen)",
        )
    )
    assert render(("noun_article",), word(see, "Gender=Fem"), article_map=ARTICLES) == {"noun_article": "die"}


def test_a_morph_gender_the_chips_rule_out_yields_to_the_dictionary():
    oma = html(block(chip("gender-feminine", "fem"), "Oma f (genitive Oma, plural Omas)"))
    assert render(("noun_gender",), word(oma, "Gender=Masc")) == {"noun_gender": "feminine"}


def test_morph_leads_when_the_dictionary_has_no_gender_chips():
    plain = html(block(chip("partOfSpeech", "n"), "Kiefer f (genitive Kiefer, plural Kiefern)"))
    assert render(("noun_gender",), word(plain, "Gender=Masc")) == {"noun_gender": "masculine"}


def test_an_ambiguous_chip_set_falls_through_to_the_head_line():
    assert render(("noun_gender",), word(FUCHS)) == {"noun_gender": "masculine"}


def test_a_single_gender_chip_answers_before_the_head_line():
    see = html(block(chip("gender-feminine", "fem"), "See m (mixed, genitive Sees, plural Seen)"))
    assert render(("noun_article",), word(see), article_map=ARTICLES) == {"noun_article": "die"}


def test_a_multi_valued_morph_gender_is_skipped():
    assert render(("noun_gender",), word(FUCHS, "Gender=Fem,Masc")) == {"noun_gender": "masculine"}


@pytest.mark.parametrize(
    ("head", "plural"),
    [
        ("Fuchs m (strong, genitive Fuchses, plural Füchse, diminutive Füchslein n)", "Füchse"),
        ("élève m or f (plural élèves)", "élèves"),
        ("Hund m (strong, genitive Hundes or Hunds, plural Hunde or (regionally) Hünde)", "Hunde"),
        ("Ersatz m (strong, genitive Ersatzes, plural (uncommon) Ersätze)", "Ersätze"),
        ("Ferien pl (plural only)", None),
        ("gens m pl (plural only)", None),
        ("ciseaux m pl (plural and singular)", None),
        ("knjȉga f (Cyrillic spelling књи̏га, plural knjige)", "knjige"),
    ],
)
def test_plural_is_read_from_the_unfolded_head_line(head, plural):
    out = render(("noun_plural",), word(html(block("", head))))
    assert out == ({} if plural is None else {"noun_plural": plural})


def test_only_the_first_dictionary_block_is_read():
    second = block(chip("gender-neuter", "neut"), "Kiefer n (plural Kiefer)", dictionary="other")
    first = block("", "Kiefer f (genitive Kiefer, plural Kiefern)")
    assert render(("noun_gender", "noun_plural"), word(html(first, second))) == {
        "noun_gender": "feminine",
        "noun_plural": "Kiefern",
    }


def test_an_or_gender_and_combining_marks_in_the_gender_letter():
    either = html(block("", "Fuchs m or f (proper noun, surname)"))
    assert render(("noun_gender",), word(either)) == {}
    assert render(("noun_gender",), word(html(block("", "knjȉga f (plural knjige)")))) == {"noun_gender": "feminine"}


def test_escaped_head_lines_are_unescaped():
    escaped = html(block("", "Rock&#x27;n&#x27;Roll m (strong, plural Rock&#x27;n&#x27;Rolls)"))
    assert render(("noun_plural",), word(escaped)) == {"noun_plural": "Rock'n'Rolls"}


def test_non_nouns_and_empty_entries_render_nothing():
    assert render(("noun_gender",), word(FUCHS, "Gender=Masc", pos="VERB")) == {}
    assert render(("noun_gender", "noun_plural"), word("")) == {}


def test_custom_labels_can_put_the_article_in_the_gender_field():
    assert render(("noun_gender",), word("", "Gender=Fem"), gender_labels={"masc": "el", "fem": "la"}) == {
        "noun_gender": "la"
    }


def test_an_article_rule_sees_the_headword_and_wins_over_the_map():
    def italian(gender: str, headword: str) -> str:
        if gender != "masc":
            return "la"
        return (
            "lo"
            if headword[:1] in "z"
            or headword[:2] in ("gn", "ps")
            or (headword[:1] == "s" and headword[1:2] not in "aeiou")
            else "il"
        )

    assert render(
        ("noun_article",),
        word("", "Gender=Masc", mined_form="studente"),
        article_map={"masc": "il"},
        article_rule=italian,
    ) == {"noun_article": "lo"}
    assert render(("noun_article",), word("", "Gender=Masc", mined_form="libro"), article_rule=italian) == {
        "noun_article": "il"
    }
    assert render(("noun_article",), word("", "Gender=Masc", mined_form="x"), article_rule=lambda g, h: "") == {}


def test_misconfiguration_is_refused():
    with pytest.raises(ValueError, match="unknown"):
        GrammarTagHook(("gender",))
    with pytest.raises(ValueError, match="article"):
        GrammarTagHook(("noun_article",))

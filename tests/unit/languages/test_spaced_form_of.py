"""The form-of front repair (``_spaced/form_of.py``) and the seven parsers that wire it.

The engine-free half drives ``FormOfLemmaPass`` with duck tokens and a stand-in for R36's
``form_lookup`` holding rows in the rendered shape the Yomitan importer stores. Every row set below
is the one the wty dictionary of that language holds for those keys (revision 2026.09.20, read
through ``storage.term_rows``), cut to the rows the rule reads. The real-engine half runs the
injected pass over the real tagger's tokens: the tagger is module-scoped because the autouse
conftest fixture clears the tagger cache around every test.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.form_of import (
    FormOfLemmaPass,
    OrderedPasses,
    form_targets,
    is_lemma_row,
    lemma_row_targets,
)
from anki_miner.languages._spaced.morphology import SeparableVerbPass, StashedParticle, stash_particle
from anki_miner.languages.token import LanguageToken

# --------------------------------------------------------------------------
# Rows in the importer's rendered shape, and a form lookup over them
# --------------------------------------------------------------------------


def lemma(tags: str) -> tuple[str, str]:
    """A headword row: the pass reads only its tags."""
    return ('<li class="gloss-item"><div class="gloss-content">a sense</div></li>', tags)


def form(*targets: str) -> tuple[str, str]:
    """A ``non-lemma`` row naming ``targets``, single- or multi-target as the importer renders them."""
    if len(targets) == 1:
        return (f'<li class="gloss-item"><div class="gloss-content">{targets[0]}</div></li>', "non-lemma")
    items = "".join(f'<li class="gloss-sc-li">{target}</li>' for target in targets)
    return (
        f'<li class="gloss-item"><div class="gloss-content"><ul class="gloss-sc-ul">{items}</ul></div></li>',
        "non-lemma",
    )


class Forms:
    """``FormLookup``: casefolded keys as ``CasefoldDictKeys`` stores them, answered under the asked spelling."""

    def __init__(self, rows: dict[str, list[tuple[str, str]]]) -> None:
        self._rows = {key.casefold(): value for key, value in rows.items()}
        self.calls: list[list[str]] = []

    def __call__(self, terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        self.calls.append(list(terms))
        return {term: self._rows[term.casefold()] for term in terms if term.casefold() in self._rows}


def tok(surface: str, pos1: str, lemma_: str) -> LanguageToken:
    return LanguageToken(surface=surface, pos1=pos1, lemma=lemma_)


def run(pass_: FormOfLemmaPass, forms: Forms | None, *tokens: LanguageToken) -> list[tuple[str, str]]:
    return [(t.feature.lemma, t.feature.pos1) for t in pass_(list(tokens), None, forms)]


# wty-sv-en: the surface's row names the noun, the tagger's lemma's row names the verb.
SV_MOTET = {"mötet": [form("möte")], "möt": [form("möta")], "möte": [lemma("n neut")], "möta": [lemma("v")]}
# wty-pl-en: an ambiguous surface, whose lemma's one target would be a different word (thread).
PL_MILI = {
    "mili": [form("mila polska"), form("mila"), form("miły")],
    "mić": [form("nić")],
    "nić": [lemma("n fem")],
    "miły": [lemma("adj")],
}


# --------------------------------------------------------------------------
# The rendered-content readers (moved here from he/morphology.py)
# --------------------------------------------------------------------------


def test_the_row_readers_read_the_rendered_shapes():
    assert form_targets(form("möte")[0]) == ["möte"]
    assert form_targets(form("kentė́ti", "kę̃sti")[0]) == ["kentė́ti", "kę̃sti"]
    assert is_lemma_row("n neut") and is_lemma_row("") and not is_lemma_row("non-lemma")
    assert lemma_row_targets(*lemma("v")) is None
    assert lemma_row_targets(*form("möte")) == ["möte"]


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


def test_without_a_dictionary_the_pass_changes_nothing():
    assert run(FormOfLemmaPass(), None, tok("fönstret", "NOUN", "fönstr")) == [("fönstr", "NOUN")]


def test_the_surface_form_row_names_the_front():
    forms = Forms({"fönstret": [form("fönster")], "fönster": [lemma("n neut")]})
    assert run(FormOfLemmaPass(), forms, tok("fönstret", "NOUN", "fönstr")) == [("fönster", "NOUN")]


def test_the_surface_is_read_before_the_lemma():
    assert run(FormOfLemmaPass(), Forms(SV_MOTET), tok("Mötet", "NOUN", "möt")) == [("möte", "NOUN")]


def test_a_surface_with_a_lemma_row_is_the_front_and_its_first_row_the_class():
    forms = Forms({"sänka": [lemma("v"), lemma("n")], "sänk": [form("sänka")]})
    assert run(FormOfLemmaPass(), forms, tok("sänka", "NOUN", "sänk")) == [("sänka", "VERB")]


def test_the_lemma_form_rows_answer_when_the_surface_has_no_rows():
    forms = Forms({"komm": [form("kommen")], "kommen": [lemma("v")]})
    assert run(FormOfLemmaPass(), forms, tok("Kommste", "VERB", "komm")) == [("kommen", "VERB")]


def test_a_lemma_that_is_a_headword_is_never_touched():
    """de ``Hochdeutsch``: a name and an adjective row, and a form row naming ``Hochdeutsche``."""
    forms = Forms({"Hochdeutsch": [lemma("name neut"), form("Hochdeutsche"), lemma("adj")]})
    assert run(FormOfLemmaPass(), forms, tok("Hochdeutsch", "NOUN", "Hochdeutsch")) == [("Hochdeutsch", "NOUN")]


def test_an_ambiguous_surface_keeps_the_token_rather_than_asking_the_lemma():
    assert run(FormOfLemmaPass(), Forms(PL_MILI), tok("mili", "ADJ", "mić")) == [("mić", "ADJ")]


def test_a_target_that_is_no_headword_keeps_the_token():
    """pl ``mailem``: its row names ``mail``, itself only a form row of ``mejl``."""
    forms = Forms({"mailem": [form("mail")], "mail": [form("mejl")], "mejl": [lemma("n masc")]})
    assert run(FormOfLemmaPass(), forms, tok("mailem", "NOUN", "mail")) == [("mail", "NOUN")]


def test_duplicate_targets_count_once():
    forms = Forms({"glömde": [form("glömma", "glömma")], "glömma": [lemma("v")]})
    assert run(FormOfLemmaPass(), forms, tok("glömde", "VERB", "glömde")) == [("glömma", "VERB")]


def test_the_class_comes_from_the_front_s_first_lemma_row():
    """de ``Hör auf!``: tagged a noun, the dictionary's first row for ``hören`` is the verb."""
    forms = Forms({"hör": [form("hören")], "hören": [lemma("v weak"), lemma("n neut")]})
    assert run(FormOfLemmaPass(), forms, tok("Hör", "NOUN", "Hör")) == [("hören", "VERB")]


def test_a_front_is_cased_like_the_tokenizer_cases_a_lemma():
    rows = {"äpfel": [form("Apfel")], "Apfel": [lemma("n masc"), lemma("name")]}
    assert run(FormOfLemmaPass(title_case_pos=frozenset({"NOUN"})), Forms(rows), tok("Äpfel", "NOUN", "Äpfel")) == [
        ("Apfel", "NOUN")
    ]
    assert run(FormOfLemmaPass(), Forms(rows), tok("Äpfel", "NOUN", "äpfel")) == [("apfel", "NOUN")]


def test_the_surface_front_is_lowered_never_casefolded():
    """The dictionary key folds ``ß`` to ``ss``; the card front must not."""
    forms = Forms({"maß": [lemma("n neut")]})
    assert run(FormOfLemmaPass(), forms, tok("Maß", "ADJ", "maße")) == [("maß", "NOUN")]


def test_only_the_content_classes_are_read():
    forms = Forms({"honom": [form("han")], "han": [lemma("pron")]})
    assert run(FormOfLemmaPass(), forms, tok("honom", "PRON", "honom"), tok("in", "ADP", "in")) == [
        ("honom", "PRON"),
        ("in", "ADP"),
    ]
    assert forms.calls == []


def test_one_read_for_the_line_one_for_its_targets_then_the_cache():
    pass_ = FormOfLemmaPass()
    forms = Forms({**SV_MOTET, "fönstret": [form("fönster")], "fönster": [lemma("n neut")]})
    line = [tok("Mötet", "NOUN", "möt"), tok("fönstret", "NOUN", "fönstr")]
    assert run(pass_, forms, *line) == [("möte", "NOUN"), ("fönster", "NOUN")]
    assert len(forms.calls) == 2
    assert set(forms.calls[1]) == {"möte", "fönster"}
    again = [tok("Mötet", "NOUN", "möt"), tok("fönstret", "NOUN", "fönstr")]
    assert run(pass_, forms, *again) == [("möte", "NOUN"), ("fönster", "NOUN")]
    assert len(forms.calls) == 2


# --------------------------------------------------------------------------
# The knobs later languages set from their own parser.py
# --------------------------------------------------------------------------


def test_lemma_first_reads_the_tagger_s_lemma_before_the_surface():
    assert run(FormOfLemmaPass(surface_first=False), Forms(SV_MOTET), tok("Mötet", "NOUN", "möt")) == [("möta", "VERB")]


def test_a_front_gate_can_refuse_a_front():
    verbs_only = FormOfLemmaPass(accept=lambda token, front, rows: any(tags.split(" ")[0] == "v" for _, tags in rows))
    assert run(verbs_only, Forms(SV_MOTET), tok("Mötet", "NOUN", "möt")) == [("möt", "NOUN")]
    forms = Forms({"glömde": [form("glömma")], "glömma": [lemma("v")]})
    assert run(verbs_only, forms, tok("glömde", "VERB", "glömde")) == [("glömma", "VERB")]


def test_same_pos_needs_a_lemma_row_of_the_token_s_class():
    """sl ``mama``: its one form row names the verb ``imeti``."""
    forms = Forms({"mama": [form("imeti")], "imeti": [lemma("v")]})
    assert run(FormOfLemmaPass(same_pos=True), forms, tok("mama", "NOUN", "mama")) == [("mama", "NOUN")]
    assert run(FormOfLemmaPass(), forms, tok("mama", "NOUN", "mama")) == [("imeti", "VERB")]


def test_extra_candidates_follow_the_surface():
    forms = Forms({"matė": [form("matyti")], "matyti": [lemma("v")]})
    strip_ne = FormOfLemmaPass(extra_candidates=lambda token: [token.surface.lower()[2:]])
    assert run(strip_ne, forms, tok("nematė", "VERB", "nematė")) == [("matyti", "VERB")]
    assert run(FormOfLemmaPass(), forms, tok("nematė", "VERB", "nematė")) == [("nematė", "VERB")]


def test_a_row_reading_can_class_a_lemma_tagged_row_as_a_form_row():
    """el: ``μιλάς`` is a lemma-tagged ``v`` row whose gloss names ``μιλάω``."""
    forms = Forms({"μιλάς": [("second-person singular present of μιλάω", "v")], "μιλάω": [lemma("v")]})

    def of_rows(content: str, tags: str) -> list[str] | None:
        return [content.rsplit(" of ", 1)[1]] if " of " in content else lemma_row_targets(content, tags)

    assert run(FormOfLemmaPass(row_targets=of_rows), forms, tok("μιλάς", "VERB", "μιλάς")) == [("μιλάω", "VERB")]


def test_ordered_passes_run_in_order_with_the_same_arguments():
    seen: list[tuple[str, object, object]] = []

    def first(tokens, attest, forms):
        seen.append(("first", attest, forms))
        return tokens

    def second(tokens, attest, forms):
        seen.append(("second", attest, forms))
        return tokens

    attest, forms = object(), object()
    OrderedPasses(first, second)([], attest, forms)
    assert seen == [("first", attest, forms), ("second", attest, forms)]


def test_the_join_sees_the_repaired_verb():
    """de ``Du siehst müde aus``: ``siehst`` becomes ``sehen`` first, so the join is ``aussehen``."""
    head = tok("siehst", "VERB", "siehst")
    stash_particle(head, StashedParticle("aus", tok("aus", "ADP", "aus"), "svp"))
    forms = Forms({"siehst": [form("sehen")], "sehen": [lemma("v")]})
    OrderedPasses(FormOfLemmaPass(), SeparableVerbPass())([head], lambda words: set(words) & {"aussehen"}, forms)
    assert head.feature.lemma == "aussehen"


def test_a_head_the_tagger_already_joined_is_left_to_the_join():
    """nl ``staat … bekend``: lemmatised ``bekendstaan``, which wty-nl-en lacks; ``staat`` is also a noun."""
    head = tok("staat", "VERB", "bekendstaan")
    head.feature.particle = "bekend"
    forms = Forms({"staat": [lemma("n masc"), form("staan")], "staan": [lemma("v")]})
    assert run(FormOfLemmaPass(), forms, head) == [("bekendstaan", "VERB")]
    assert forms.calls == []


# --------------------------------------------------------------------------
# The parsers that wire it
# --------------------------------------------------------------------------


def _injected(monkeypatch, code: str):
    from anki_miner.languages.registry import get_profile

    seen: dict[str, object] = {}

    def fake(config, **kwargs):
        seen.update(kwargs)
        return "parser"

    monkeypatch.setattr("anki_miner.languages._spaced.create_spaced_parser", fake)
    assert get_profile(code).create_parser(AnkiMinerConfig()) == "parser"
    return seen["token_post_pass"]


@pytest.mark.parametrize("code", ["de", "nl", "sv", "nb", "da"])
def test_the_particle_languages_repair_before_they_join(monkeypatch, code):
    injected = _injected(monkeypatch, code)
    assert isinstance(injected, OrderedPasses)
    repair, join = injected._passes  # noqa: SLF001 - the order is the contract
    assert isinstance(repair, FormOfLemmaPass) and isinstance(join, SeparableVerbPass)


@pytest.mark.parametrize("code", ["pl", "lt"])
def test_pl_and_lt_wire_the_repair_alone(monkeypatch, code):
    assert isinstance(_injected(monkeypatch, code), FormOfLemmaPass)


def test_german_fronts_take_the_tokenizer_s_title_case_classes(monkeypatch):
    from anki_miner.languages.de.tokenizer import DE_TITLE_CASE_POS

    repair, _join = _injected(monkeypatch, "de")._passes  # noqa: SLF001
    assert repair._title_case_pos is DE_TITLE_CASE_POS  # noqa: SLF001


def test_lithuanian_targets_lose_their_stress_marks(monkeypatch):
    """wty-lt-en keys every lemma row unstressed and names many targets twice, plain and stressed."""
    forms = Forms({"paliko": [form("palikti"), form("pali̇̀kti")], "palikti": [lemma("v")]})
    assert run(_injected(monkeypatch, "lt"), forms, tok("paliko", "VERB", "palikoti")) == [("palikti", "VERB")]


# --------------------------------------------------------------------------
# The real taggers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def taggers():
    from anki_miner.languages.de.tokenizer import build_tagger as de_tagger
    from anki_miner.languages.nl.tokenizer import build_tagger as nl_tagger
    from anki_miner.languages.pl.tokenizer import build_tagger as pl_tagger
    from anki_miner.languages.sv.tokenizer import build_tagger as sv_tagger

    return {"de": de_tagger(), "nl": nl_tagger(), "sv": sv_tagger(), "pl": pl_tagger()}


def _fronts(monkeypatch, taggers, code, line, rows, attested=frozenset()):
    tokens = taggers[code](line)
    _injected(monkeypatch, code)(tokens, lambda words: set(words) & set(attested), Forms(rows))
    return {token.surface: (token.feature.lemma, token.feature.pos1) for token in tokens}


def test_de_real_line_joins_the_repaired_verb(monkeypatch, taggers):
    rows = {"siehst": [form("sehen")], "sehen": [lemma("v")], "müde": [lemma("adj")]}
    fronts = _fronts(monkeypatch, taggers, "de", "Du siehst müde aus.", rows, {"aussehen"})
    assert fronts["siehst"] == ("aussehen", "VERB")


def test_de_real_plural_noun_keeps_its_capital(monkeypatch, taggers):
    rows = {"äpfel": [form("Apfel")], "Apfel": [lemma("n masc"), lemma("name")], "drei": [lemma("num")]}
    fronts = _fronts(monkeypatch, taggers, "de", "Ich habe drei Äpfel gekauft.", rows)
    assert fronts["Äpfel"] == ("Apfel", "NOUN")


def test_nl_real_particle_verb_the_tagger_joined_stays(monkeypatch, taggers):
    rows = {"staat": [lemma("n masc"), form("staan")], "staan": [lemma("v")]}
    line = "Het Schotse hoogland staat bekend om zijn ruige schoonheid."
    assert _fronts(monkeypatch, taggers, "nl", line, rows)["staat"] == ("bekendstaan", "VERB")


def test_sv_real_sentence_initial_noun(monkeypatch, taggers):
    fronts = _fronts(monkeypatch, taggers, "sv", "Mötet började sent.", SV_MOTET)
    assert fronts["Mötet"] == ("möte", "NOUN")


def test_pl_real_verb(monkeypatch, taggers):
    rows = {"zapomniałeś": [form("zapomnieć")], "zapomnieć": [lemma("v pf")]}
    fronts = _fronts(monkeypatch, taggers, "pl", "Zapomniałeś kluczy?", rows)
    assert fronts["Zapomniałeś"] == ("zapomnieć", "VERB")

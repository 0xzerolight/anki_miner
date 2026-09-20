"""PymorphyLemmaRepair: the shared pymorphy3 token post-pass (ru's repair, its two policies injected).

The analyser is a stub here on purpose: the class owns the POLICY (when does the identity branch
fire, which spelling is the analyser asked for, what happens when the answer is ambiguous), and the
real-engine consequences of each policy are pinned in the ru and uk tokenizer suites.
"""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.pymorphy import PymorphyLemmaRepair
from anki_miner.languages._spaced.script import strip_cyrillic_stress
from anki_miner.languages.ru.morphology import RuLemmaRepair
from anki_miner.languages.token import LanguageToken

ACUTE = "\N{COMBINING ACUTE ACCENT}"
RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"


class _Parse:
    def __init__(self, normal_form: str, tag: str, is_known: bool = True) -> None:
        self.normal_form, self.tag, self.is_known = normal_form, tag, is_known


class _Analyzer:
    def __init__(self, table: dict[str, list[_Parse]]) -> None:
        self.table = table
        self.asked: list[str] = []

    def parse(self, word: str) -> list[_Parse]:
        self.asked.append(word)
        return self.table.get(word, [])


def _to_upos(tag: str) -> tuple[str, dict[str, str]]:
    return {"ADVB": ("ADV", {}), "NOUN": ("NOUN", {"Case": "Nom"}), "VERB": ("VERB", {})}[str(tag)]


def _token(surface: str, lemma: str, pos: str) -> LanguageToken:
    return LanguageToken(surface=surface, pos1=pos, lemma=lemma)


def _apostrophe_blind(text: str) -> str:
    return text.replace(RSQUO, "'").lower()


def _canonical(text: str) -> str:
    return text.replace(RSQUO, "'")


def test_a_hyphenated_token_takes_pymorphy3s_first_known_parse():
    repair = PymorphyLemmaRepair()
    repair.bind(_Analyzer({"по-українськи": [_Parse("по-українськи", "ADVB")]}), _to_upos)
    (token,) = repair([_token("по-українськи", "по-українськи", "NOUN")])
    assert (token.feature.pos1, token.feature.lemma, token.morph) == ("ADV", "по-українськи", "")


def test_a_hyphenated_token_pymorphy3_does_not_know_keeps_the_models_answer():
    repair = PymorphyLemmaRepair()
    repair.bind(_Analyzer({"диван-кровать": [_Parse("x", "NOUN", is_known=False)]}), _to_upos)
    (token,) = repair([_token("диван-кровать", "диван-кровать", "NOUN")])
    assert token.feature.lemma == "диван-кровать"


def test_an_identity_lemma_takes_the_unique_same_pos_normal_form():
    repair = PymorphyLemmaRepair()
    repair.bind(_Analyzer({"сховалася": [_Parse("сховатися", "VERB")]}), _to_upos)
    (token,) = repair([_token("сховалася", "сховалася", "VERB")])
    assert token.feature.lemma == "сховатися"


def test_two_candidates_fall_back_to_the_lower_cased_surface():
    repair = PymorphyLemmaRepair()
    repair.bind(_Analyzer({"Міст": [_Parse("міст", "NOUN"), _Parse("місто", "NOUN")]}), _to_upos)
    (token,) = repair([_token("Міст", "Міст", "NOUN")])
    assert token.feature.lemma == "міст"


def test_a_token_outside_allowed_pos_is_left_alone():
    repair = PymorphyLemmaRepair(allowed_pos=("VERB",))
    repair.bind(_Analyzer({"стіл": [_Parse("стіл", "NOUN")]}), _to_upos)
    (token,) = repair([_token("стіл", "стіл", "NOUN")])
    assert token.feature.lemma == "стіл"


def test_the_default_fold_is_case_only_and_rus_fold_is_yo_blind():
    """The one behavioural difference between the base class and ru's subclass."""
    analyzer = _Analyzer({"ёлка": [_Parse("ёлка", "NOUN")], "Ёлка": [_Parse("ёлка", "NOUN")]})
    plain, russian = PymorphyLemmaRepair(), RuLemmaRepair()
    plain.bind(analyzer, _to_upos)
    russian.bind(analyzer, _to_upos)
    # lemma "елка" vs surface "ёлка": identical only under the yo-blind fold, so only ru relemmatises.
    assert plain([_token("ёлка", "елка", "NOUN")])[0].feature.lemma == "елка"
    assert russian([_token("ёлка", "елка", "NOUN")])[0].feature.lemma == "ёлка"


def test_the_identity_branch_asks_the_analyser_for_the_analysis_form():
    """uk plan P6a: the analyser is asked for the canonical spelling, never the line's own.

    Without this the candidate set is empty for every typographic surface - ``pymorphy3-dicts-uk``
    knows only U+0027 - and the fallback writes back the inflected form the branch exists to fix.
    """
    analyzer = _Analyzer({"м'яча": [_Parse("м'яч", "NOUN")]})
    repair = PymorphyLemmaRepair(fold=_apostrophe_blind, analysis_form=_canonical)
    repair.bind(analyzer, _to_upos)
    (token,) = repair([_token(f"м{RSQUO}яча", "м'яча", "NOUN")])
    assert analyzer.asked == ["м'яча"] and token.feature.lemma == "м'яч"


def test_the_hyphen_branch_asks_the_analyser_for_the_analysis_form_too():
    analyzer = _Analyzer({"ПО-УКРАЇНСЬКИ": [_Parse("по-українськи", "ADVB")]})
    repair = PymorphyLemmaRepair(analysis_form=str.upper)
    repair.bind(analyzer, _to_upos)
    (token,) = repair([_token("по-українськи", "по-українськи", "NOUN")])
    assert analyzer.asked == ["ПО-УКРАЇНСЬКИ"]
    assert (token.feature.pos1, token.feature.lemma) == ("ADV", "по-українськи")


def test_the_ambiguous_fallback_writes_the_analysis_form_not_the_raw_surface():
    """uk plan P6a: two candidates, so the fallback fires - and it must not smuggle U+2019 in."""
    repair = PymorphyLemmaRepair(fold=_apostrophe_blind, analysis_form=_canonical)
    repair.bind(_Analyzer({"Зв'язок": [_Parse("зв'язка", "NOUN"), _Parse("зв'язок", "NOUN")]}), _to_upos)
    (token,) = repair([_token(f"Зв{RSQUO}язок", "зв'язок", "NOUN")])
    assert token.feature.lemma == "зв'язок"


def test_the_default_analysis_form_leaves_ru_asking_for_the_raw_surface():
    """Ruling condition 2: ru passes no analysis_form, so its lookups and its yo fallback stand."""
    analyzer = _Analyzer({"Ёлка": []})
    russian = RuLemmaRepair()
    russian.bind(analyzer, _to_upos)
    (token,) = russian([_token("Ёлка", "елка", "NOUN")])
    assert analyzer.asked == ["Ёлка"]
    assert token.feature.lemma == "ёлка"


def test_an_unbound_repair_refuses_to_run():
    with pytest.raises(RuntimeError):
        PymorphyLemmaRepair()([_token("а", "а", "NOUN")])


@pytest.mark.parametrize("written", ["чита" + ACUTE + "ла", "читала"])
def test_the_shared_stress_strip_is_idempotent(written):
    assert strip_cyrillic_stress(written) == strip_cyrillic_stress(strip_cyrillic_stress(written)) == "читала"


def test_a_latin_accent_survives_the_cyrillic_stress_strip():
    assert strip_cyrillic_stress("café") == "café"

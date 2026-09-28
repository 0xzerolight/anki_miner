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


#: The stub tags: an OpenCorpora POS (plus ``voct`` where the case matters), mapped as ``oc2ud`` maps it.
_UPOS: dict[str, tuple[str, dict[str, str]]] = {
    "ADJF": ("ADJ", {"Case": "Nom"}),
    "ADJF ablt": ("ADJ", {"Case": "Ins", "Gender": "Fem", "Number": "Sing"}),
    "ADJF plur": ("ADJ", {"Case": "Nom", "Number": "Plur"}),
    "ADJS femn": ("ADJ", {"Gender": "Fem", "Number": "Sing", "Variant": "Brev"}),
    "ADJS masc": ("ADJ", {"Gender": "Masc", "Number": "Sing", "Variant": "Brev"}),
    "ADJS neut": ("ADJ", {"Gender": "Neut", "Number": "Sing", "Variant": "Brev"}),
    "ADVB": ("ADV", {}),
    "COMP": ("ADJ", {"Degree": "Cmp"}),
    "GRND": ("VERB", {"VerbForm": "Conv"}),
    "INFN": ("VERB", {"VerbForm": "Inf"}),
    "NOUN": ("NOUN", {"Case": "Nom"}),
    "NOUN voct": ("NOUN", {"Case": "Voc"}),
    "NUMR": ("NUM", {"Case": "Gen"}),
    "PRTF": ("VERB", {"VerbForm": "Part"}),
    "VERB": ("VERB", {"Mood": "Imp", "VerbForm": "Fin"}),
}


def _to_upos(tag: str) -> tuple[str, dict[str, str]]:
    return _UPOS[str(tag)]


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


@pytest.mark.parametrize("pos", ["NOUN", "PROPN", "ADJ"])
def test_a_word_the_analyser_knows_only_as_a_verb_is_retagged_a_verb(pos):
    """A sentence-initial imperative the model tags NOUN (Подожди), PROPN (Смотри) or ADJ (Закрий)."""
    repair = PymorphyLemmaRepair()
    repair.bind(_Analyzer({"Подожди": [_Parse("подождать", "VERB")]}), _to_upos)
    (token,) = repair([_token("Подожди", "подожди", pos)])
    assert (token.feature.pos1, token.feature.lemma, token.morph) == ("VERB", "подождать", "Mood=Imp|VerbForm=Fin")


def test_two_verbs_behind_one_imperative_front_the_surface_not_the_first_parse():
    """Стой is стоять's imperative and стоить's: the first parse would front 'to cost'."""
    repair = PymorphyLemmaRepair()
    repair.bind(_Analyzer({"Стой": [_Parse("стоить", "VERB"), _Parse("стоять", "VERB")]}), _to_upos)
    (token,) = repair([_token("Стой", "стой", "PROPN")])
    assert (token.feature.pos1, token.feature.lemma) == ("VERB", "стой")


def test_an_infinitive_counts_as_a_verb_parse():
    repair = PymorphyLemmaRepair()
    repair.bind(_Analyzer({"набути": [_Parse("набути", "INFN")]}), _to_upos)
    (token,) = repair([_token("набути", "набути", "NOUN")])
    assert (token.feature.pos1, token.feature.lemma) == ("VERB", "набути")


def test_the_verb_retag_asks_the_analyser_for_the_analysis_form():
    analyzer = _Analyzer({"З'їж": [_Parse("з'їсти", "VERB")]})
    repair = PymorphyLemmaRepair(fold=_apostrophe_blind, analysis_form=_canonical)
    repair.bind(analyzer, _to_upos)
    (token,) = repair([_token(f"З{RSQUO}їж", f"з{RSQUO}їж", "NOUN")])
    assert analyzer.asked == ["З'їж"] and (token.feature.pos1, token.feature.lemma) == ("VERB", "з'їсти")


@pytest.mark.parametrize(
    "parses",
    [
        [_Parse("дышать", "GRND")],  # a gerund is not a finite verb (дыша)
        [_Parse("читать", "PRTF")],  # nor is a participle, which the model rightly tags ADJ
        [_Parse("сталь", "NOUN"), _Parse("стать", "VERB")],  # a noun parse keeps the model's noun
        [_Parse("дотку", "VERB", is_known=False)],  # an unknown word is no evidence at all
    ],
)
def test_a_word_with_any_other_parse_keeps_the_models_pos(parses):
    repair = PymorphyLemmaRepair(allowed_pos=("VERB",))
    repair.bind(_Analyzer({"слово": parses}), _to_upos)
    (token,) = repair([_token("слово", "слово", "NOUN")])
    assert (token.feature.pos1, token.feature.lemma) == ("NOUN", "слово")


@pytest.mark.parametrize(
    "surface,lemma,parses",
    [
        ("Можна", "можний", [_Parse("можний", "ADJF")]),  # pymorphy3-dicts-uk knows можна only as an adjective
        ("уже", "уж", [_Parse("уж", "NOUN voct")]),  # ... and уже only as the vocative of уж
        ("варто", "варта", [_Parse("варта", "NOUN voct"), _Parse("варта", "NOUN voct")]),
        ("пытливо", "пытливый", [_Parse("пытливый", "ADJS neut")]),  # the short neuter is the adverb's spelling
    ],
)
def test_an_adverb_fronting_a_word_it_is_no_form_of_takes_its_own_spelling(surface, lemma, parses):
    repair = PymorphyLemmaRepair()
    repair.bind(_Analyzer({surface: parses}), _to_upos)
    (token,) = repair([_token(surface, lemma, "ADV")])
    assert (token.feature.pos1, token.feature.lemma) == ("ADV", surface.lower())


def test_the_adverb_repair_writes_the_analysis_form():
    repair = PymorphyLemmaRepair(fold=_apostrophe_blind, analysis_form=_canonical)
    repair.bind(_Analyzer({"Обов'язково": [_Parse("обов'язковий", "ADJF")]}), _to_upos)
    (token,) = repair([_token(f"Обов{RSQUO}язково", "обов'язковий", "ADV")])
    assert token.feature.lemma == "обов'язково"


@pytest.mark.parametrize(
    "surface,lemma,morph,parses",
    [
        ("громче", "громкий", "Degree=Cmp", [_Parse("громкий", "COMP")]),  # ru36: a comparative fronts its adjective
        ("Раньше", "ранний", "", [_Parse("ранний", "COMP")]),  # ... also where only the parse says so
        ("врёте", "врать", "", [_Parse("врать", "VERB")]),  # a verb the model tagged ADV
        ("тринадцати", "тринадцать", "", [_Parse("тринадцать", "NUMR")]),  # ... and a numeral
        ("щодня", "щодень", "", [_Parse("щодня", "ADVB"), _Parse("щодень", "NOUN")]),  # an adverb parse exists
        ("ранком", "ранок", "", [_Parse("ранок", "NOUN")]),  # a noun parse that is not a vocative
        ("глуп", "глупый", "", [_Parse("глупый", "ADJS masc")]),  # a short adjective the model tagged ADV
        ("смешна", "смешной", "", [_Parse("смешной", "ADJS femn")]),
        ("внутреннею", "внутренний", "", [_Parse("внутренний", "ADJF ablt")]),  # an oblique case
        ("сиромудрі", "сиромудрий", "", [_Parse("сиромудрий", "ADJF plur")]),  # a plural
    ],
)
def test_an_adverb_lemma_the_analyser_can_account_for_is_left_alone(surface, lemma, morph, parses):
    repair = PymorphyLemmaRepair()
    repair.bind(_Analyzer({surface: parses}), _to_upos)
    token = _token(surface, lemma, "ADV")
    token.morph = morph
    (token,) = repair([token])
    assert (token.feature.pos1, token.feature.lemma) == ("ADV", lemma)


def test_a_token_no_branch_can_change_never_reaches_the_analyser():
    """An inflected noun or name (never a mistagged verb: spaCy lemmatises one to its own text) and
    a comparative adverb cost no parse."""
    analyzer = _Analyzer({})
    repair = PymorphyLemmaRepair()
    repair.bind(analyzer, _to_upos)
    comparative = _token("громче", "громкий", "ADV")
    comparative.morph = "Degree=Cmp"
    repair([_token("столе", "стол", "NOUN"), _token("Москве", "москва", "PROPN"), comparative])
    assert analyzer.asked == []


def test_an_unbound_repair_refuses_to_run():
    with pytest.raises(RuntimeError):
        PymorphyLemmaRepair()([_token("а", "а", "NOUN")])


@pytest.mark.parametrize("written", ["чита" + ACUTE + "ла", "читала"])
def test_the_shared_stress_strip_is_idempotent(written):
    assert strip_cyrillic_stress(written) == strip_cyrillic_stress(strip_cyrillic_stress(written)) == "читала"


def test_a_latin_accent_survives_the_cyrillic_stress_strip():
    assert strip_cyrillic_stress("café") == "café"

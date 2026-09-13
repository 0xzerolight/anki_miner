"""Portuguese data and the enclisis helpers (no model)."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.pt.morphology import (
    L_CLITICS,
    PT_ABBREVIATIONS,
    PT_ALLOWED_POS,
    PT_CLITICS,
    PT_EXCLUDED_SUBTYPES,
    PT_GENDER_LABELS,
    PT_LEADING_WORDS,
    PT_POST_PASSES,
    enclitic_copy,
    infinitive_before_l_clitic,
    pt_dedup_fold,
    whole_word_lemma,
)
from anki_miner.languages.token import LanguageToken
from anki_miner.services.reading.sentence_splitter import split_sentences

#: Entries not in spaCy's Portuguese tokenizer exceptions (D14): titles, incl. the English ones subtitles keep.
ADDITIONS = frozenset({"dra", "srta", "prof", "profa", "mrs", "ms"})


def test_pos_gate_is_the_shared_upos_set_and_no_fine_tags():
    assert PT_ALLOWED_POS == UPOS_ALLOWED
    assert PT_EXCLUDED_SUBTYPES == ()


def test_abbreviations_come_from_spacy_minus_real_words():
    from spacy.lang.pt.tokenizer_exceptions import TOKENIZER_EXCEPTIONS
    from spacy.lang.tokenizer_exceptions import BASE_EXCEPTIONS

    spacy_keys = {
        text[:-1].casefold()
        for text in TOKENIZER_EXCEPTIONS
        if text.endswith(".") and len(text) > 1 and text not in BASE_EXCEPTIONS
    }
    assert PT_ABBREVIATIONS - spacy_keys == ADDITIONS
    assert spacy_keys - PT_ABBREVIATIONS == {"dom"}  # an ordinary word: "Ela tem um dom." ends its sentence
    assert all(key == key.casefold() and not key.endswith(".") for key in PT_ABBREVIATIONS)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("O Sr. Silva chegou. Depois saiu.", ["O Sr. Silva chegou.", "Depois saiu."]),
        ("A Dra. Costa atendeu. Foi rápido.", ["A Dra. Costa atendeu.", "Foi rápido."]),
        ("A Mrs. Smith chegou. Depois saiu.", ["A Mrs. Smith chegou.", "Depois saiu."]),
        ("Ela tem um dom. Depois cantou.", ["Ela tem um dom.", "Depois cantou."]),
    ],
)
def test_the_splitter_keeps_a_title_inside_its_sentence(text, expected):
    assert split_sentences(text, rules=sentence_rules(PT_ABBREVIATIONS)) == expected


def test_gender_labels_are_the_articles():
    assert dict(PT_GENDER_LABELS) == {"masc": "o", "fem": "a"}
    assert frozenset({"o", "a", "os", "as", "um", "uma", "uns", "umas"}) == PT_LEADING_WORDS


@pytest.mark.parametrize(
    ("text", "copy", "starts"),
    [
        ("Dá-me o guarda-chuva.", "dá me o guarda-chuva.", {3}),
        ("Ela levantou-se cedo.", "Ela levantou se cedo.", {13}),
        ("Vou vendê-lo e dá-mo.", "Vou vendê lo e dá mo.", {10, 18}),
        ("Diga-lhe que sim.", "diga lhe que sim.", {5}),
        ("Vende-se casa, Guiné-Bissau, e-mail.", "vende se casa, Guiné-Bissau, e-mail.", {6}),
        # a clitic-shaped middle is not a clitic: the chain continues
        ("O bem-te-vi e o louva-a-deus.", "O bem-te-vi e o louva-a-deus.", set()),
        ("A luta do dia-a-dia.", "A luta do dia-a-dia.", set()),
        # proclisis needs nothing; mesoclisis is a documented miss
        ("Me dá o guarda-chuva.", "Me dá o guarda-chuva.", set()),
        ("Dir-te-ei a verdade.", "Dir-te-ei a verdade.", set()),
    ],
)
def test_enclitic_copy(text, copy, starts):
    result, found = enclitic_copy(text)
    assert (result, set(found)) == (copy, starts)
    assert len(result) == len(text)


def test_the_clitic_table_holds_the_l_allomorphs():
    assert set(PT_CLITICS) >= L_CLITICS
    assert len(set(PT_CLITICS)) == len(PT_CLITICS)


@pytest.mark.parametrize(
    ("host", "infinitive"),
    [
        ("fazê", "fazer"), ("comprá", "comprar"), ("Comprá", "comprar"), ("parti", "partir"), ("compô", "compor"),
        ("vê", "ver"), ("ouvi", "ouvir"), ("fá", "fazer"), ("fi", "fazer"), ("di", "dizer"), ("trá", "trazer"),
        ("qui", "querer"), ("pô", "pôr"), ("comemo", None), ("casa", None), ("a", None),
    ],
)  # fmt: skip
def test_infinitive_before_l_clitic(host, infinitive):
    assert infinitive_before_l_clitic(host) == infinitive


def _base(text: str) -> str:
    return text.casefold().strip(" .")


@pytest.mark.parametrize(
    ("front", "key"),
    [("levantar-se", "levantar"), ("Levantar-se.", "levantar"), ("ir-se", "ir"), ("se", "se"), ("o-se", "o-se"),
     ("chamar-se-á", "chamar-se-á"), ("a casa-se", "a casa-se"), ("livro", "livro")],
)  # fmt: skip
def test_the_reflexive_tail_folds_away_idempotently(front, key):
    fold = pt_dedup_fold(_base)
    assert fold(front) == key
    assert fold(fold(front)) == fold(front)


def test_a_fused_adverb_fronts_its_own_spelling():
    """D20: the model lemmatises daqui as "de aqui"; a card front never carries a space."""
    tokens = [
        LanguageToken("Daqui", "ADV", lemma="de aqui"),
        LanguageToken("do", "ADP", lemma="de o"),  # never mines: left alone
        LanguageToken("livros", "NOUN", lemma="livro"),
    ]
    assert whole_word_lemma(tokens) is tokens
    assert [t.feature.lemma for t in tokens] == ["daqui", "de o", "livro"]
    assert (whole_word_lemma,) == PT_POST_PASSES

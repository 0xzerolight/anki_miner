"""Ukrainian tokenizer over the REAL uk_core_news_sm + pymorphy3 (plan P3, P5, P6, P6a).

Module-scoped tagger: conftest resets the tagger cache per test, and reloading the model for every
case would cost minutes.
"""

from __future__ import annotations

import pytest

from anki_miner.languages.uk.tokenizer import build_tagger

RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def test_the_pipeline_is_the_mining_minimum(tagger):
    assert tagger.nlp.pipe_names == ["tok2vec", "morphologizer", "attribute_ruler", "lemmatizer"]
    assert tagger.nlp.meta["lang"] == "uk"


def test_the_repair_reuses_the_lemmatizers_analyser(tagger):
    """One MorphAnalyzer: a second would load the 14 MB dictionaries twice.

    It is also the ru/uk separation at engine level - the same surface, a different normal form.
    """
    analyser = tagger.nlp.get_pipe("lemmatizer")._morph
    assert analyser.parse("читала")[0].normal_form == "читати"


@pytest.mark.parametrize(
    "text,surface,pos,lemma",
    [
        ("Він розмовляє по-українськи з нами.", "по-українськи", "ADV", "по-українськи"),
        ("Вона зайшла в інтернет-магазин.", "інтернет-магазин", "NOUN", "інтернет-магазин"),
        ("Синьо-жовтий прапор висів над майданом.", "Синьо-жовтий", "ADJ", "синьо-жовтий"),
        ("Ми встали рано-вранці.", "рано-вранці", "ADV", "рано-вранці"),
    ],
)
def test_a_hyphenated_word_is_one_token_tagged_by_pymorphy3(tagger, text, surface, pos, lemma):
    """P5: the model splits these and mis-tags the joined form; pymorphy3 knows them as whole words."""
    token = next(t for t in tagger(text) if t.surface == surface)
    assert (token.feature.pos1, token.feature.lemma) == (pos, lemma)


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        ("Кішка сховалася під ліжком.", "сховалася", "сховатися"),
        ("Ґудзик відірвався від пальта.", "пальта", "пальто"),
    ],
)
def test_an_identity_lemma_takes_pymorphy3s_unique_normal_form(tagger, text, surface, lemma):
    token = next(t for t in tagger(text) if t.surface == surface)
    assert token.feature.lemma == lemma


@pytest.mark.parametrize(
    "text,surface,pos,lemma",
    [
        (f"Він грає у м{RSQUO}яч.", f"м{RSQUO}яч", "NOUN", "м'яч"),
        (f"Здоров{RSQUO}я найдорожче.", f"Здоров{RSQUO}я", "NOUN", "здоров'я"),
        (f"Вона з{RSQUO}їла торт.", f"з{RSQUO}їла", "VERB", "з'їсти"),
        (f"Ми п{RSQUO}ємо каву.", f"п{RSQUO}ємо", "VERB", "пити"),
        ("Він грає у м'яч.", "м'яч", "NOUN", "м'яч"),
    ],
)
def test_the_apostrophe_tagging_copy(tagger, text, surface, pos, lemma):
    """P3: the copy folds to U+0027 for the engine, the surface keeps the line's own character."""
    token = next(t for t in tagger(text) if t.surface == surface)
    assert (token.feature.pos1, token.feature.lemma) == (pos, lemma)


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        (f"Він кинув м{RSQUO}яча через високий паркан.", f"м{RSQUO}яча", "м'яч"),
        ("Він кинув м'яча через високий паркан.", "м'яча", "м'яч"),
        (f"Зв{RSQUO}язок обірвався раптово і назавжди.", f"Зв{RSQUO}язок", "зв'язок"),
        (f"Вони розповіли про подвір{RSQUO}я.", f"подвір{RSQUO}я", "подвір'я"),
    ],
)
def test_an_apostrophe_identity_lemma_is_repaired_on_the_canonical_spelling(tagger, text, surface, lemma):
    """P6a: the model's lemma comes back in U+0027 while the surface keeps U+2019, so the identity
    test must be apostrophe-blind AND the analyser must be asked for the canonical spelling.

    Measured with neither: the genitive `м'яча` ships as the card front. Measured with only the
    blind fold: all four of these front with a typographic apostrophe, because pymorphy3 answers
    `is_known=False` for every parse and the ambiguous fallback writes the raw surface back.
    """
    token = next(t for t in tagger(text) if t.surface == surface)
    assert token.feature.lemma == lemma


def test_a_real_word_before_a_final_dot_is_not_an_abbreviation(tagger):
    """P10: `м.` is a spaCy exception and `м` is a real word, so the rule is pruned."""
    token = next(t for t in tagger("Це моя м.") if t.surface == "м")
    assert token.feature.pos1 != "X"


def test_an_abbreviation_keeps_its_dot(tagger):
    assert "обл." in {t.surface for t in tagger("Вона живе у Київській обл. давно.")}


def test_pos2_is_dead(tagger):
    """uk_core_news_sm has no fine tagset, which is why UK_EXCLUDED_SUBTYPES is empty."""
    assert {t.feature.pos2 for t in tagger("Студентка читала цікаві книжки.")} == {""}


def test_the_surfaces_cover_the_line(tagger):
    text = f"Він грає у м{RSQUO}яч із друзями."
    assert "".join(t.surface for t in tagger(text)) == text.replace(" ", "")


def test_every_surface_is_a_verbatim_slice_of_the_line(tagger):
    """The tagging copy must never reach the card: the apostrophe on screen is the one typed."""
    text = f"Здоров{RSQUO}я найдорожче, каже п{RSQUO}ятий пацієнт."
    assert all(t.surface in text for t in tagger(text))
    assert f"Здоров{RSQUO}я" in {t.surface for t in tagger(text)}

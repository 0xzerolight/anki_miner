"""The spaCy adapter: dist-info-free loading, pipeline composition, hyphen infix, copies. Real spaCy."""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
from types import SimpleNamespace

import pytest

from anki_miner.languages._spaced import tokenizer
from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.services.tagger import LockedTagger


@pytest.fixture
def sys_path_root(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    created: list[str] = []
    yield tmp_path, created
    for name in created:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()


def test_a_dist_info_less_model_package_loads_through_the_adapter(sys_path_root):
    import spacy

    root, created = sys_path_root
    package = root / "zz_fake_sm"
    package.mkdir()
    nlp = spacy.blank("en")
    nlp.meta.update({"name": "fake_sm", "version": "0.0.1"})
    nlp.to_disk(package / "en_fake_sm-0.0.1")
    (package / "meta.json").write_text(
        (package / "en_fake_sm-0.0.1" / "meta.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (package / "__init__.py").write_text(
        "from spacy.util import load_model_from_init_py\n\n\ndef load(**overrides):\n"
        "    return load_model_from_init_py(__file__, **overrides)\n",
        encoding="utf-8",
    )
    created.append("zz_fake_sm")
    assert not list(root.glob("*.dist-info"))
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.distribution("zz_fake_sm")

    loaded = tokenizer.load_spacy_model("zz_fake_sm", keep_parser=False)
    tokens = tokenizer.SpacyTagger(loaded)("Hello  world")

    assert [t.surface for t in tokens] == ["Hello", "world"]


def test_the_pipeline_is_meta_minus_ner_senter_and_an_unkept_parser(sys_path_root):
    root, created = sys_path_root
    (root / "zz_record_sm").mkdir()
    (root / "zz_record_sm" / "__init__.py").write_text(
        "def load(**overrides):\n    return overrides\n", encoding="utf-8"
    )
    created.append("zz_record_sm")

    assert tokenizer.load_spacy_model("zz_record_sm", keep_parser=False) == {"exclude": ["ner", "senter", "parser"]}
    assert tokenizer.load_spacy_model("zz_record_sm", keep_parser=True) == {"exclude": ["ner", "senter"]}


@pytest.fixture(scope="module")
def english():
    return tokenizer.build_spacy_tagger("en_core_web_sm", join_hyphenated=True, tag_char_map=APOSTROPHE_FOLD)


def test_build_returns_a_locked_tagger_over_the_real_model(english):
    assert isinstance(english, LockedTagger)
    assert english.nlp.pipe_names == ["tok2vec", "tagger", "attribute_ruler", "lemmatizer"]


def test_surfaces_cover_the_line_verbatim(english):
    for line in ("It's a well-known fact that e-mail is faster.", "I don’t know, you’re right.", "THE END"):
        tokens = english(line)
        assert "".join(t.surface for t in tokens) == line.replace(" ", "")


def test_hyphen_compounds_stay_whole_and_splitting_is_the_default():
    tokens = tokenizer.build_spacy_tagger("en_core_web_sm")("a well-known fact")
    assert [t.surface for t in tokens] == ["a", "well", "-", "known", "fact"]


def test_joined_hyphen_compounds_and_pos2_and_morph(english):
    assert {t.surface: t.feature.pos1 for t in english("It's a well-known fact.")}["well-known"] == "ADJ"
    went = {t.surface: t for t in english("She went to the market.")}["went"]
    assert (went.feature.pos1, went.feature.pos2, went.feature.lemma) == ("VERB", "VBD", "go")
    assert "Tense=Past" in went.morph


def test_curly_contractions_are_tagged_from_the_folded_copy(english):
    features = {t.surface: t.feature for t in english("I don’t know, you’re right.")}
    assert features["n’t"].pos1 == "PART"
    assert features["’re"].pos1 == "AUX" and features["’re"].lemma == "be"


def test_an_all_caps_cue_is_tagged_lowercased_but_keeps_its_surfaces(english):
    tokens = english("THE END")
    assert [(t.surface, t.feature.pos1, t.feature.lemma) for t in tokens] == [
        ("THE", "DET", "the"),
        ("END", "NOUN", "end"),
    ]


def test_urls_are_not_vocabulary(english):
    assert [t.feature.pos1 for t in english("see www.example.com") if t.surface == "www.example.com"] == ["X"]


def test_post_passes_run_in_order_after_the_duck_tokens(sys_path_root):
    import spacy

    order: list[str] = []

    def first(tokens):
        order.append("first")
        tokens[0].feature.lemma = "repaired"
        return tokens

    def second(tokens):
        order.append("second")
        return [t for t in tokens if t.surface != "drop"]

    tagger = tokenizer.SpacyTagger(spacy.blank("en"), post_passes=(first, second))
    tokens = tagger("keep drop")
    assert order == ["first", "second"]
    assert [(t.surface, t.feature.lemma) for t in tokens] == [("keep", "repaired")]


def test_dash_glued_words_split_while_hyphen_compounds_stay_whole(english):
    assert [t.surface for t in english("I don't know—really.")][3:6] == ["know", "—", "really"]
    assert [t.surface for t in english("Wait--what?")] == ["Wait", "--", "what", "?"]
    assert {"well-known", "e-mail"} <= {t.surface for t in english("A well-known e-mail.")}


def test_a_hyphen_rule_is_detected_by_behaviour_whatever_its_spelling():
    italian_style = r"(?<=[a-zà-ù])(?:-)(?=[a-zà-ù])"  # not en's literal; it ships a differently written rule
    nlp = SimpleNamespace(
        Defaults=SimpleNamespace(infixes=[italian_style, r"(?<=[0-9])[+*^](?=[0-9-])"]), tokenizer=SimpleNamespace()
    )
    tokenizer._configure_infixes(nlp, join_hyphenated=True)
    assert [m.group() for m in nlp.tokenizer.infix_finditer("ab-cd 1+2 so—cd")] == ["+", "—"]


def test_word_exceptions_outside_the_abbreviation_set_are_pruned():
    nlp = SimpleNamespace(
        tokenizer=SimpleNamespace(rules={"hand.": [1], "Hand.": [2], "Dr.": [3], "z.B.": [4], "don't": [5]})
    )
    tokenizer._prune_dotted_rules(nlp, frozenset({"dr"}))
    assert set(nlp.tokenizer.rules) == {"Dr.", "z.B.", "don't"}


def test_a_sentence_final_word_keeps_its_own_token_and_a_title_stays_whole():
    tagger = tokenizer.build_spacy_tagger("en_core_web_sm", abbreviations=frozenset({"mr"}))
    assert [t.surface for t in tagger("We went to Mass. Mr. Smith left.")] == [
        "We",
        "went",
        "to",
        "Mass",
        ".",
        "Mr.",
        "Smith",
        "left",
        ".",
    ]
    untouched = tokenizer.build_spacy_tagger("en_core_web_sm")
    assert "Mass." in [t.surface for t in untouched("We went to Mass.")]


def test_a_split_off_abbreviation_dot_never_yields_a_front():
    spanish = tokenizer.build_spacy_tagger("es_core_news_sm")
    tokens = {t.surface: t.feature.pos1 for t in spanish("a las 10 a.m. mañana")}
    assert tokens.get("a.m") == "X" or tokens.get("a.m.") == "X"


def test_a_model_without_a_dash_rule_gets_one_and_keeps_its_compounds():
    german = tokenizer.build_spacy_tagger("de_core_news_sm")
    assert [t.surface for t in german("so—wirklich")] == ["so", "—", "wirklich"]
    assert [t.surface for t in german("Die E-Mail kam.")][1] == "E-Mail"


def test_argument_errors():
    with pytest.raises(ValueError, match="parser"):
        tokenizer.build_spacy_tagger("en_core_web_sm", particle_deps=frozenset({"svp"}))
    with pytest.raises(ValueError, match="one character"):
        tokenizer.SpacyTagger(SimpleNamespace(), tag_char_map={"’": "''"})
    with pytest.raises(ValueError, match="hyphen"):
        tokenizer._configure_infixes(
            SimpleNamespace(Defaults=SimpleNamespace(infixes=["x"]), tokenizer=SimpleNamespace()), join_hyphenated=True
        )

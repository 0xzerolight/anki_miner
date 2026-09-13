"""Duck tokens from spaCy-shaped tokens (stubs, no engine) and the separable-verb post-pass."""

from __future__ import annotations

from types import SimpleNamespace

from anki_miner.languages._spaced.morphology import SeparableVerbPass
from anki_miner.languages._spaced.tokens import to_duck_tokens


def fake_doc(text: str, rows: list[tuple]) -> list[SimpleNamespace]:
    """rows: (text, pos, tag, lemma, dep, head_index[, morph[, like_url]]); idx found left to right in *text*."""
    tokens: list[SimpleNamespace] = []
    cursor = 0
    lowered = text.lower()
    for i, row in enumerate(rows):
        word, pos, tag, lemma, dep, _head = row[:6]
        idx = lowered.index(word.lower(), cursor)
        cursor = idx + len(word)
        tokens.append(
            SimpleNamespace(
                i=i, idx=idx, text=word, pos_=pos, tag_=tag, lemma_=lemma, dep_=dep,
                morph=row[6] if len(row) > 6 else "", is_space=False,
                like_url=row[7] if len(row) > 7 else False, like_email=False, head=None,
            )
        )  # fmt: skip
    for token, row in zip(tokens, rows, strict=True):
        token.head = tokens[row[5]]
    return tokens


def test_surfaces_are_sliced_from_the_original_line():
    text = "THE END"
    doc = fake_doc(text, [("the", "DET", "DT", "the", "det", 1), ("end", "NOUN", "NN", "end", "ROOT", 1)])
    tokens = to_duck_tokens(doc, text)
    assert [t.surface for t in tokens] == ["THE", "END"]
    assert [(t.feature.pos1, t.feature.pos2, t.feature.lemma, t.feature.kana) for t in tokens] == [
        ("DET", "DT", "the", ""),
        ("NOUN", "NN", "end", ""),
    ]


def test_pos2_is_blank_when_the_fine_tag_repeats_the_coarse_one_and_morph_is_carried():
    text = "went home"
    doc = fake_doc(
        text,
        [
            ("went", "VERB", "VBD", "go", "ROOT", 0, "Tense=Past|VerbForm=Fin"),
            ("home", "NOUN", "NOUN", "home", "obj", 0),
        ],
    )
    went, home = to_duck_tokens(doc, text)
    assert went.feature.pos2 == "VBD" and went.morph == "Tense=Past|VerbForm=Fin"  # morph is a token slot (Stage S)
    assert home.feature.pos2 == ""


def test_dotted_abbreviations_become_x_and_a_sentence_final_word_does_not():
    text = "Dr. Smith, e.g. at 3 p.m."
    rows = [
        ("Dr.", "NOUN", "NN", "Dr.", "nk", 1),
        ("Smith", "PROPN", "NNP", "Smith", "ROOT", 1),
        ("e.g.", "ADV", "RB", "e.g.", "advmod", 1),
        ("3", "NUM", "CD", "3", "nummod", 4),
        ("p.m.", "NOUN", "NN", "p.m.", "npadvmod", 1),
    ]
    tokens = to_duck_tokens(fake_doc(text, rows), text)
    assert [t.feature.pos1 for t in tokens] == ["X", "PROPN", "X", "NUM", "X"]
    assert [t.feature.pos1 for t in to_duck_tokens(fake_doc("go", [("go", "VERB", "VB", "go", "ROOT", 0)]), "go")] == [
        "VERB"
    ]


def test_an_abbreviation_whose_final_dot_was_split_off_is_x_too():
    """NOTE 012: es/pt/it/fr/ca/de tokenizers emit ``a.m`` + ``.``; the internal letter-dot-letter shape counts."""
    text = "a.m . p.ex 3.5 etc."
    rows = [
        ("a.m", "NOUN", "NOUN", "a.m", "obl", 0),
        (".", "PUNCT", "PUNCT", ".", "punct", 0),
        ("p.ex", "VERB", "VERB", "p.ex", "ROOT", 2),
        ("3.5", "NUM", "NUM", "3.5", "nummod", 2),
        ("etc.", "ADV", "ADV", "etc.", "advmod", 2),
    ]
    assert [t.feature.pos1 for t in to_duck_tokens(fake_doc(text, rows), text)] == ["X", "PUNCT", "X", "NUM", "X"]


def test_space_tokens_are_dropped_and_urls_become_x():
    text = "see  www.example.com"
    doc = fake_doc(
        text,
        [
            ("see", "VERB", "VB", "see", "ROOT", 0),
            ("www.example.com", "NOUN", "NN", "www.example.com", "obj", 0, "", True),
        ],
    )
    doc.insert(
        1,
        SimpleNamespace(
            i=9,
            idx=3,
            text=" ",
            pos_="SPACE",
            tag_="_SP",
            lemma_=" ",
            dep_="dep",
            morph="",
            is_space=True,
            like_url=False,
            like_email=False,
            head=doc[0],
        ),
    )
    tokens = to_duck_tokens(doc, text)
    assert [t.surface for t in tokens] == ["see", "www.example.com"]
    assert tokens[1].feature.pos1 == "X"


def test_casing_follows_the_title_case_classes():
    text = "Haus London Laufen"
    doc = fake_doc(
        text,
        [
            ("Haus", "NOUN", "NN", "haus", "ROOT", 0),
            ("London", "PROPN", "NE", "London", "nk", 0),
            ("Laufen", "VERB", "VVINF", "Laufen", "nk", 0),
        ],
    )
    tokens = to_duck_tokens(doc, text, title_case_pos=frozenset({"NOUN"}))
    assert [t.feature.lemma for t in tokens] == ["Haus", "London", "laufen"]


def test_a_particle_is_stashed_on_its_verb_head_and_demoted():
    text = "Er sieht den Film an"
    rows = [
        ("Er", "PRON", "PPER", "er", "sb", 1),
        ("sieht", "VERB", "VVFIN", "sehen", "ROOT", 1),
        ("den", "DET", "ART", "der", "nk", 3),
        ("Film", "NOUN", "NN", "Film", "oa", 1),
        ("an", "ADP", "PTKVZ", "an", "svp", 1),
    ]
    tokens = to_duck_tokens(fake_doc(text, rows), text, particle_deps=frozenset({"svp"}))
    assert tokens[1].feature.particle == "an"
    assert tokens[4].feature.pos1 == "PART"
    assert not getattr(tokens[3].feature, "particle", "")


def test_no_deps_means_no_stash_and_a_non_verb_head_is_ignored():
    text = "Er sieht an"
    rows = [
        ("Er", "PRON", "PPER", "er", "sb", 1),
        ("sieht", "NOUN", "NN", "sieht", "ROOT", 1),
        ("an", "ADP", "PTKVZ", "an", "svp", 1),
    ]
    assert not getattr(to_duck_tokens(fake_doc(text, rows), text)[1].feature, "particle", "")
    stashed = to_duck_tokens(fake_doc(text, rows), text, particle_deps=frozenset({"svp"}))
    assert not getattr(stashed[1].feature, "particle", "") and stashed[2].feature.pos1 == "ADP"


def _stashed_tokens():
    text = "Er sieht den Film an"
    rows = [
        ("Er", "PRON", "PPER", "er", "sb", 1),
        ("sieht", "VERB", "VVFIN", "sehen", "ROOT", 1),
        ("den", "DET", "ART", "der", "nk", 3),
        ("Film", "NOUN", "NN", "Film", "oa", 1),
        ("an", "ADP", "PTKVZ", "an", "svp", 1),
    ]
    return to_duck_tokens(fake_doc(text, rows), text, particle_deps=frozenset({"svp"}))


def test_the_pass_reattaches_unconditionally_without_a_dictionary():
    tokens = SeparableVerbPass()(_stashed_tokens(), None, None)
    assert tokens[1].feature.lemma == "ansehen"
    assert not tokens[1].feature.particle  # cleared: a second run cannot prefix again
    assert SeparableVerbPass()(tokens, None, None)[1].feature.lemma == "ansehen"


def test_the_pass_needs_the_headword_when_a_dictionary_is_wired():
    calls: list[list[str]] = []

    def attest(words: list[str]) -> set[str]:
        calls.append(words)
        return set()

    tokens = SeparableVerbPass()(_stashed_tokens(), attest, None)
    assert tokens[1].feature.lemma == "sehen"
    assert calls == [["ansehen"]]
    assert SeparableVerbPass()(_stashed_tokens(), lambda words: {"ansehen"}, None)[1].feature.lemma == "ansehen"


def test_a_line_without_particles_makes_no_probe():
    def attest(words: list[str]) -> set[str]:
        raise AssertionError("probed")

    tokens = to_duck_tokens(
        fake_doc("go home", [("go", "VERB", "VB", "go", "ROOT", 0), ("home", "NOUN", "NN", "home", "obj", 0)]),
        "go home",
    )
    assert SeparableVerbPass()(tokens, attest, None) is tokens

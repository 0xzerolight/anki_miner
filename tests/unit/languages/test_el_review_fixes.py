"""Greek review fixes: the enclitic accent fold, the closed-class retag, form-row fronts and X/PROPN recovery.

The engine-free half drives the pieces with duck tokens and rows in the rendered shape the Yomitan
importer stores (the wty-el-en 2026.08.29 rows for those keys, cut to what the rule reads). The
real-engine half runs ``el_core_news_sm``; its tagger is module-scoped because the autouse conftest
fixture clears the tagger cache around every test.
"""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.form_of import FormOfLemmaPass
from anki_miner.languages.el.morphology import EL_CLOSED_CLASS, fold_enclitic_accent
from anki_miner.languages.el.parser import greek_row_targets
from anki_miner.languages.el.tokenizer import retag_greek_tokens
from anki_miner.languages.token import LanguageToken

# --------------------------------------------------------------------------
# EL-02: the enclitic second accent, folded in the tagging copy
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("το αυτοκίνητό μου", "το αυτοκίνητο μου"),
        ("Άκουσέ με", "Άκουσε με"),
        ("τα πράγματά σου", "τα πράγματα σου"),
        ("πίστεψέ με", "πίστεψε με"),
        ("νωρίς—αλλά", "νωρίς—αλλά"),
        ("το όνομα μου", "το όνομα μου"),
        ("ΑΥΤΟΚΙΝΗΤΟ", "ΑΥΤΟΚΙΝΗΤΟ"),
        ("προϊόντα", "προϊόντα"),
    ],
)
def test_a_letter_run_keeps_only_its_first_accent(text, folded):
    assert fold_enclitic_accent(text) == folded
    assert len(fold_enclitic_accent(text)) == len(text)


def test_the_tagger_hook_tags_the_folded_copy_and_slices_the_original(el_tagger):
    tokens = {token.surface: token.feature for token in el_tagger("Το αυτοκίνητό μου χάλασε.")}
    assert (tokens["αυτοκίνητό"].pos1, tokens["αυτοκίνητό"].lemma) == ("NOUN", "αυτοκίνητο")
    tokens = {token.surface: token.feature for token in el_tagger("Τα πράγματά σου είναι εδώ.")}
    assert (tokens["πράγματά"].pos1, tokens["πράγματά"].lemma) == ("NOUN", "πράγμα")


def test_a_line_initial_imperative_before_an_enclitic_is_a_verb(el_tagger):
    listen, _me, _dot = el_tagger("Άκουσέ με.")
    assert (listen.surface, listen.feature.pos1) == ("Άκουσέ", "VERB")


def test_the_shared_hook_runs_after_the_character_map():
    import spacy

    from anki_miner.languages._spaced.tokenizer import SpacyTagger

    blank = spacy.blank("el")
    seen: list[str] = []

    def nlp(text: str):
        seen.append(text)
        return blank(text)

    tagger = SpacyTagger(nlp, tag_char_map={"’": "'"}, tag_fold=fold_enclitic_accent)
    tokens = tagger("σ’ το αυτοκίνητό")
    assert seen == ["σ' το αυτοκίνητο"]
    assert [token.surface for token in tokens] == ["σ’", "το", "αυτοκίνητό"]


# --------------------------------------------------------------------------
# EL-04: closed-class words the model tags as content
# --------------------------------------------------------------------------


def tok(surface: str, pos1: str, lemma_: str = "") -> LanguageToken:
    return LanguageToken(surface=surface, pos1=pos1, lemma=lemma_ or surface.lower())


def _retagged(*tokens: LanguageToken) -> list[tuple[str, str]]:
    return [(token.surface, token.feature.pos1) for token in retag_greek_tokens(list(tokens))]


def test_a_closed_class_word_tagged_as_content_is_retagged():
    assert _retagged(tok("σου", "NOUN"), tok("Εσύ", "VERB"), tok("Μην", "ADV"), tok("Ποιος", "ADJ")) == [
        ("σου", "PRON"),
        ("Εσύ", "PRON"),
        ("Μην", "PART"),
        ("Ποιος", "PRON"),
    ]
    assert _retagged(tok("είσαι", "ADV"), tok("Ήμασταν", "PROPN"), tok("που", "ADV")) == [
        ("είσαι", "AUX"),
        ("Ήμασταν", "AUX"),
        ("που", "PRON"),
    ]


def test_the_table_is_accent_sensitive():
    """Interrogative ``πού`` (where) is an adverb; ``κάνεις`` (you do) is a verb, ``κανείς`` (nobody) a pronoun."""
    assert EL_CLOSED_CLASS["που"] == "PRON" and "πού" not in EL_CLOSED_CLASS
    assert EL_CLOSED_CLASS["κανείς"] == "PRON" and "κάνεις" not in EL_CLOSED_CLASS
    assert _retagged(tok("πού", "ADV"), tok("κάνεις", "NOUN")) == [("πού", "ADV"), ("κάνεις", "NOUN")]


def test_a_closed_class_tag_the_model_chose_is_kept():
    """``το`` is an article (DET) or a clitic (PRON); only a content, X or PROPN tag is the model's mistake."""
    assert _retagged(tok("το", "DET"), tok("με", "ADP"), tok("δεν", "PART")) == [
        ("το", "DET"),
        ("με", "ADP"),
        ("δεν", "PART"),
    ]


def test_the_retag_keeps_the_lemma():
    (token,) = retag_greek_tokens([tok("σου", "NOUN", "σου")])
    assert token.feature.lemma == "σου"


@pytest.mark.parametrize(
    ("sentence", "surface", "pos"),
    [
        ("Τα πράγματά σου είναι εδώ.", "σου", "PRON"),
        ("Εσύ τι λες;", "Εσύ", "PRON"),
        ("Μην ανησυχείς.", "Μην", "PART"),
        ("Ποιος είναι;", "Ποιος", "PRON"),
        ("Είσαι καλά;", "Είσαι", "AUX"),
        ("Ήμασταν εκεί.", "Ήμασταν", "AUX"),
    ],
)
def test_the_real_tagger_retags_the_closed_classes(el_tagger, sentence, surface, pos):
    assert {token.surface: token.feature.pos1 for token in el_tagger(sentence)}[surface] == pos


# --------------------------------------------------------------------------
# EL-01: fronts from wty-el-en's form rows, and its lemma-tagged inflection rows
# --------------------------------------------------------------------------

_TAG_SG = (
    '<div class="gloss-sc-div" data-sc-content="tags"><span class="gloss-sc-span" data-sc-content="tag" '
    'data-sc-category="number" title="singular">sg</span></div>'
)
_EXAMPLE = (
    '<details class="gloss-sc-details" data-sc-content="details-entry-examples"><summary class="gloss-sc-summary" '
    'data-sc-content="summary-entry">1 example</summary><div class="gloss-sc-div" data-sc-content="extra-info">'
    '<div class="gloss-sc-div" data-sc-content="example-sentence-a">το όνομά μου είναι …</div></div></details>'
)


def head(tags: str, *glosses: str) -> tuple[str, str]:
    """A lemma-tagged row in the rendered shape: preamble, the glosses list, the backlink."""
    items = "".join(f'<li class="gloss-sc-li"><div class="gloss-sc-div">{gloss}</div></li>' for gloss in glosses)
    return (
        '<li class="gloss-item"><div class="gloss-content"><div class="gloss-sc-div"><div class="gloss-sc-div" '
        'data-sc-content="preamble"><details class="gloss-sc-details" data-sc-content="details-entry-Grammar">'
        '<summary class="gloss-sc-summary" data-sc-content="summary-entry">Grammar</summary>'
        '<div class="gloss-sc-div" data-sc-content="Grammar-content">λέξη • (léxi)</div></details></div></div>'
        f'<ol class="gloss-sc-ol" data-sc-content="glosses">{items}</ol><div class="gloss-sc-div" '
        'data-sc-content="backlink"><a class="gloss-sc-a" href="https://en.wiktionary.org/wiki/λέξη#Greek">'
        "Wiktionary</a></div></div></li>",
        tags,
    )


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
    """``FormLookup``: casefolded keys as ``CasefoldDictKeys`` stores them (``πήγεσ``), answered under the asked spelling."""

    def __init__(self, rows: dict[str, list[tuple[str, str]]]) -> None:
        self._rows = {key.casefold(): value for key, value in rows.items()}

    def __call__(self, terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        return {term: self._rows[term.casefold()] for term in terms if term.casefold() in self._rows}


ECHEIS = head("v sg", "second-person singular present of έχω (écho): &quot;you have&quot;")
EINAI = head("v", f"{_TAG_SG}third-person singular present of είμαι (eímai): &quot;he is&quot;{_EXAMPLE}")
EL_ROWS: dict[str, list[tuple[str, str]]] = {
    "έχεις": [ECHEIS, form("έχω")],
    "έχω": [head("v", "to have")],
    "πήγες": [form("πηγαίνω", "πηγαίνω")],
    "πηγαίνω": [head("v", "to go")],
    "χθες": [head("adv", "yesterday")],
    "χρόνο": [form("χρόνος")],
    "χρόνος": [head("n masc", "time"), head("n masc", "year")],
    "θόρυβος": [head("n masc", "noise")],
    "τηλέφωνο": [head("n neut", "telephone, phone")],
    # EL-03: cue-initial content words tagged X/PROPN, and the names that must stay out
    "κλείσε": [form("κλείνω")],
    "κλείνω": [head("v", "to close"), form("κλείνομαι")],
    "πόρτα": [head("n fem", "door")],
    "θυμάσαι": [head("v sg", "second-person singular present of θυμάμαι (thymámai)"), form("θυμάμαι", "θυμάμαι")],
    "θυμάμαι": [head("v", "to remember")],
    "καλά": [head("adv", "well"), form("καλός", "καλός"), form("καλό", "καλό")],
    "καλημέρα": [head("intj", "good morning"), head("n fem", "good morning, hello")],
    "μαρία": [head("name fem", "Mary, a female given name")],
    "σοφία": [head("n fem", "wisdom"), head("name fem", "a female given name, Sofia")],
    "γιάννης": [form("Ιωάννης")],
    "ιωάννης": [head("name masc", "a male given name, John")],
}


@pytest.mark.parametrize(
    ("row", "targets"),
    [
        (ECHEIS, ["έχω"]),
        (EINAI, ["είμαι"]),
        (
            head("v pf sg", "second-person singular perfective imperative of παίρνω (paírno): &quot;take&quot;"),
            ["παίρνω"],
        ),
        (head("v", "active nonfinite form of βλασταίνω (vlastaíno)"), ["βλασταίνω"]),
        (head("n neut pl", "nominative/accusative/vocative plural of αδέλφι (adélfi), siblings"), ["αδέλφι"]),
        (
            head(
                "v",
                "colloquial variation of συντριφτήκαν (syntriftíkan), third-person plural simple past of "
                "συντρίβομαι (syntrívomai)",
            ),
            ["συντρίβομαι"],
        ),
        (form("πηγαίνω", "πηγαίνω"), ["πηγαίνω", "πηγαίνω"]),
    ],
)
def test_an_inflection_row_names_its_lemma_whatever_its_tags(row, targets):
    assert greek_row_targets(*row) == targets


@pytest.mark.parametrize(
    "row",
    [
        head("v", "to write"),
        head("v", "passive of δηλώνω (dilóno): &quot;be reported, be stated&quot;"),
        head("v", "synonym of γυρίζω (gyrízo)"),
        head("v", "a more formal variant of μιλάω (miláo)"),
        head("v indecl ptcpl", "present participle of βλέπω (vlépo): seeing, observing"),
        head("n dim neut", "diminutive of καφές (kafés): a small cup of coffee"),
        head(
            "v", f"{_TAG_SG}third-person singular present of βρέχω (vrécho) he/she/it dampens", "&quot;it rains&quot;"
        ),
    ],
)
def test_a_row_with_a_sense_of_its_own_is_a_headword(row):
    assert greek_row_targets(*row) is None


def _el_pass(monkeypatch):
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.languages.registry import get_profile

    seen: dict[str, object] = {}

    def fake(config, **kwargs):
        seen.update(kwargs)
        return "parser"

    monkeypatch.setattr("anki_miner.languages._spaced.create_spaced_parser", fake)
    assert get_profile("el").create_parser(AnkiMinerConfig()) == "parser"
    return seen["token_post_pass"]


def _fronts(monkeypatch, *tokens: LanguageToken, rows=EL_ROWS) -> list[tuple[str, str]]:
    return [(t.feature.lemma, t.feature.pos1) for t in _el_pass(monkeypatch)(list(tokens), None, Forms(rows))]


def test_the_greek_parser_wires_the_form_row_repair(monkeypatch):
    assert isinstance(_el_pass(monkeypatch), FormOfLemmaPass)


def test_an_inflected_verb_fronts_its_lemma_and_takes_the_verb_class(monkeypatch):
    """``έχεις`` tagged ADJ carried a noun gender on its card; its lemma-tagged row reads "... of έχω"."""
    assert _fronts(monkeypatch, tok("Έχεις", "ADJ", "έχεις"), tok("πήγες", "ADJ", "πήγα")) == [
        ("έχω", "VERB"),
        ("πηγαίνω", "VERB"),
    ]


def test_a_surface_front_keeps_its_final_sigma(monkeypatch):
    """The dictionary key folds ``ς`` to ``σ``; the card front must not."""
    assert _fronts(monkeypatch, tok("Θόρυβος", "NOUN", "θόρυβο")) == [("θόρυβος", "NOUN")]


def test_a_surface_with_an_enclitic_accent_is_read_in_its_dictionary_spelling(monkeypatch):
    """The model lemmatises the folded ``τηλέφωνο`` as ``τηλέφωνος``, and ``τηλέφωνό`` is no dictionary key."""
    assert _fronts(monkeypatch, tok("τηλέφωνό", "NOUN", "τηλέφωνος")) == [("τηλέφωνο", "NOUN")]


def test_the_real_parser_cards_a_noun_before_an_enclitic(el_parser):
    fronts = {word.surface: word.mined_form for word in _words(el_parser, "Το τηλέφωνό μου χάλασε.")}
    assert fronts["τηλέφωνό"] == "τηλέφωνο"


def test_without_a_dictionary_the_greek_pass_changes_nothing(monkeypatch):
    assert [
        (t.feature.lemma, t.feature.pos1) for t in _el_pass(monkeypatch)([tok("πήγες", "ADJ", "πήγα")], None, None)
    ] == [("πήγα", "ADJ")]


def test_the_real_parser_fronts_the_dictionary_lemma(el_parser):
    fronts = {word.surface: (word.mined_form, word.pos) for word in _words(el_parser, "Πού πήγες χθες;")}
    assert fronts["πήγες"] == ("πηγαίνω", "VERB")
    fronts = {word.surface: (word.mined_form, word.pos) for word in _words(el_parser, "Έχεις χρόνο;")}
    assert fronts == {"Έχεις": ("έχω", "VERB"), "χρόνο": ("χρόνος", "NOUN")}


# --------------------------------------------------------------------------
# EL-03: content words tagged X/PROPN, recovered through the dictionary
# --------------------------------------------------------------------------


def test_a_cue_initial_word_tagged_x_or_propn_takes_its_dictionary_front(monkeypatch):
    assert _fronts(
        monkeypatch,
        tok("Κλείσε", "PROPN", "Κλείσε"),
        tok("Θυμάσαι", "PROPN", "Θυμάσαι"),
        tok("Καλά", "PROPN", "Καλά"),
    ) == [("κλείνω", "VERB"), ("θυμάμαι", "VERB"), ("καλά", "ADV")]


def test_a_name_stays_out(monkeypatch):
    """``Μαρία`` is a name row; ``σοφία`` (wisdom) also has one; ``Γιάννης`` names ``Ιωάννης``."""
    assert _fronts(
        monkeypatch,
        tok("Μαρία", "X", "μαρία"),
        tok("Σοφία", "PROPN", "Σοφία"),
        tok("Γιάννης", "PROPN", "Γιάννη"),
    ) == [("μαρία", "X"), ("Σοφία", "PROPN"), ("Γιάννη", "PROPN")]


def test_a_capitalised_target_stays_out_even_when_filed_as_a_noun(monkeypatch):
    rows = {"έλληνες": [form("Έλληνας")], "Έλληνας": [head("n masc", "Greek (person)")]}
    assert _fronts(monkeypatch, tok("Έλληνες", "PROPN", "Έλληνες"), rows=rows) == [("Έλληνες", "PROPN")]
    assert _fronts(monkeypatch, tok("Έλληνες", "NOUN", "έλληνες"), rows=rows) == [("έλληνας", "NOUN")]


def test_a_recovered_word_must_become_a_content_word(monkeypatch):
    """``Καλημέρα`` tagged X: its first headword row is an interjection, so it stays out."""
    assert _fronts(monkeypatch, tok("Καλημέρα", "X", "καλημέρα")) == [("καλημέρα", "X")]


def test_the_real_parser_recovers_a_cue_initial_verb_and_keeps_names_out(el_parser):
    assert {(word.mined_form, word.pos) for word in _words(el_parser, "Κλείσε την πόρτα.")} == {
        ("κλείνω", "VERB"),
        ("πόρτα", "NOUN"),
    }
    names = {"Μαρία", "Σοφία", "Γιάννης"}
    for line in ("Μαρία, έλα εδώ.", "Σοφία, φύγε!", "Γιάννης, πού είσαι;"):
        assert not names & {word.surface for word in _words(el_parser, line)}, line


# --------------------------------------------------------------------------
# Real engine
# --------------------------------------------------------------------------


def _words(parser, sentence: str):
    from anki_miner.models.reading import ReadingUnit

    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=sentence, index=0, location_label="t")], False)
    return words


@pytest.fixture(scope="module")
def el_parser():
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.languages.registry import get_profile
    from anki_miner.languages.switching import switch_language

    return get_profile("el").create_parser(switch_language(AnkiMinerConfig(), "el"), form_lookup=Forms(EL_ROWS))


@pytest.fixture(scope="module")
def el_tagger():
    from anki_miner.languages.el.tokenizer import build_tagger

    return build_tagger()

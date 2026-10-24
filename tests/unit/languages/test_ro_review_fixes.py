"""Romanian review fixes: fronts from wty-ro-en's form rows (ROM-06, A1-01/02/05), „…” quotes in Reading (ROM-07).

ROM-06: wty-ro-en keys a form row without diacritics and stores the real spelling as its reading (``lasa`` / ``lasă``
-> ``lăsa``), so the Romanian keys declare the reading column for the form lookup. The parser runs three passes over
those rows: a verb the tagger lemmatised to a non-verb headword (``Pot`` -> ``pot`` "pot") takes the one verb its
surface's form rows name (A1-02); the shared form-of repair fronts the headword of any content word (A1-01); and a
line-initial or lowercase PROPN takes a lowercase, nameless headword (A1-05). The index below holds the rows wty-ro-en
holds for these keys (term, reading, tags and named targets; the glosses cut), written and read through the Romanian
keys. The tagger is module-scoped because the autouse conftest fixture clears the tagger cache around every test.
"""

from __future__ import annotations

import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.registry import get_profile
from anki_miner.languages.ro.morphology import RO_ABBREVIATIONS, ro_fold_cedilla
from anki_miner.languages.ro.parser import (
    RomanianHomographPass,
    RomanianNonHeadwordFront,
    RomanianPropnRecovery,
    ro_row_targets,
)
from anki_miner.languages.token import LanguageToken
from anki_miner.services.dictionary import storage
from anki_miner.services.dictionary.storage import DictRow
from anki_miner.services.dictionary.yomitan_renderer import render_glossary_entry
from anki_miner.services.reading.sentence_splitter import split_sentences

KEYS = get_profile("ro").dict_keys


def _form_of(term: str, reading: str | None, *targets: str) -> DictRow:
    return DictRow(
        term=term,
        reading=reading,
        content=render_glossary_entry([[target, ["form-of"]] for target in targets]),
        tags="non-lemma",
    )


def _lemma(term: str, tags: str) -> DictRow:
    return DictRow(term=term, reading=None, content=f"<div>{term}</div>", tags=tags)


def _glosses(term: str, tags: str, *glosses: str) -> DictRow:
    """A headword row in the stored shape of a wty-ro-en sense list (``<ol data-sc-content="glosses">``)."""
    items = [{"tag": "li", "content": gloss} for gloss in glosses]
    content = render_glossary_entry(
        [{"type": "structured-content", "content": {"tag": "ol", "data": {"content": "glosses"}, "content": items}}]
    )
    return DictRow(term=term, reading=None, content=content, tags=tags)


#: wty-ro-en's rows for the keys the lines below read. ``lasa`` also keys ``lașă`` (a form of ``laș``), so only
#: the reading tells the spelling ``lasă`` apart.
WTY_RO = [
    _form_of("lasa", "lasă", "lăsa"),
    _form_of("lasa", "lașă", "laș"),
    _form_of("lasa", "lăsă", "lăsa"),
    _lemma("lăsa", "v"),
    _lemma("laș", "adj"),
    _form_of("caut", None, "căuta"),
    _form_of("cauta", "căuta", "căta"),
    _form_of("cauta", "caută", "căuta"),
    _lemma("căuta", "v vt"),
    _form_of("mananca", "mănâncă", "mânca"),
    _lemma("mânca", "v"),
    _form_of("mint", None, "minți"),
    _lemma("minți", "v"),
    _form_of("intelegi", "înțelegi", "înțelege"),
    _lemma("înțelege", "v"),
    _form_of("carti", "cărți", "carte", "carte", "carte"),
    _lemma("carte", "n fem"),
    _form_of("carte", None, "cartă", "cartă", "cartă"),
    _lemma("pace", "n"),
    # A1-01: noun and adverb fronts. ``tată`` is lemmatised ``tat``, whose rows name only ``țâță`` and ``tot``.
    _form_of("garile", "gările", "gară"),
    _lemma("gară", "n fem"),
    _form_of("gara", None, "gară", "gară"),
    _lemma("tată", "n masc"),
    _form_of("tat", "țâț", "țâță", "țâță", "țâță"),
    _form_of("tat", "tăt", "tot", "tot", "tot"),
    _form_of("cheile", None, "cheie"),
    _lemma("cheie", "adj fem indecl masc neut"),
    _lemma("cheie", "n fem"),
    _lemma("cheie", "name masc"),
    _lemma("aseară", "adv"),
    _form_of("aseara", "aseară", "asară"),
    _lemma("asară", "adv"),
    # A1-02: verbs lemmatised to a noun headword, and ``vii``, whose rows name three lemmas.
    _lemma("pot", "n neut"),
    _form_of("pot", None, "putea", "putea"),
    _lemma("putea", "v"),
    _lemma("sun", "n neut obs"),
    _form_of("sun", None, "suna", "suna"),
    _lemma("suna", "v"),
    _form_of("vii", None, "vie", "vie", "vie"),
    _form_of("vii", None, "viu"),
    _form_of("vii", None, "veni"),
    _lemma("viu", "adj masc neut"),
    _form_of("viu", None, "vie", "vie"),
    _lemma("vie", "n fem"),
    _form_of("vie", None, "viu"),
    _lemma("veni", "v"),
    _form_of("vine", None, "vână", "vână", "vână"),
    _form_of("vine", None, "veni"),
    # A1-05: a cue-initial verb tagged PROPN, and names.
    _form_of("astept", "aștept", "aștepta", "aștepta"),
    _lemma("aștepta", "v"),
    _lemma("viorel", "adj masc neut"),
    _lemma("ionel", "name masc"),
    _lemma("ion", "n masc"),
    _lemma("ion", "name masc"),
    _form_of("ion", None, "Ioana"),
    _form_of("ion", None, "Ioan"),
    # Inflection glosses: kaikki files these forms as headwords as well as by their form row.
    _glosses("vorbim", "v pl", "first-person plural present indicative of vorbi: we speak"),
    _form_of("vorbim", None, "vorbi"),
    _glosses("vorbești", "v sg", "second-person singular present indicative of vorbi: you speak"),
    _form_of("vorbesti", "vorbești", "vorbi"),
    _lemma("vorbi", "v"),
    _glosses("fii", "n pl", "plural of fiu: sons"),
    _glosses("fii", "v sg", "second-person singular imperative of fi: be"),
    _form_of("fii", None, "fi", "fi"),
    _form_of("fii", None, "fie", "fie", "fie"),
    _form_of("fii", None, "fiu", "fiu"),
    _lemma("fi", "v"),
    _lemma("fiu", "n masc"),
    _glosses("mită", "n fem", "bribe"),
    # Lemmas no headword (or only a name) whose surface names several targets, one of them of the token's class.
    _form_of("scapi", None, "scap", "scap"),
    _form_of("scapi", None, "scăpa", "scăpa"),
    _lemma("scap", "n masc"),
    _lemma("scăpa", "v"),
    _form_of("tii", "ții", "ținea"),
    _form_of("tii", "ții", "ține"),
    _form_of("tinea", "ținea", "ține"),
    _lemma("ține", "v"),
    _lemma("uite", "intj"),
    _form_of("uite", None, "uita"),
    _form_of("ui", None, "u", "u"),
    _form_of("ui", None, "vui", "vui"),
    _lemma("uita", "v"),
    _lemma("sucești", "name masc"),
    _form_of("sucesti", "sucești", "suci"),
    _form_of("sucesc", None, "suci", "suci"),
    _lemma("suci", "v"),
    _form_of("prostule", None, "prost"),
    _lemma("prostu", "name masc"),
    _lemma("prost", "adj"),
    _form_of("vina", None, "vână"),
    _form_of("vina", None, "veni"),
    _form_of("vina", None, "vină", "vină"),
    _form_of("vina", None, "vâna"),
    _lemma("vână", "n fem"),
    _lemma("vină", "n fem"),
    _lemma("vâna", "v"),
    # Lemmas a headword of another class only: ``taci`` -> ``tacă`` (silence), ``noaptea`` (at night).
    _form_of("taci", None, "tacă"),
    _form_of("taci", None, "tăcea", "tăcea", "tăcea"),
    _lemma("tacă", "n fem"),
    _form_of("taca", "tacă", "tăcea"),
    _lemma("tăcea", "v"),
    _lemma("noaptea", "adv"),
    _form_of("noaptea", None, "noapte", "noapte"),
    _lemma("noapte", "n fem"),
]


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    """A real index of the rows above, written through the Romanian keys."""
    db = tmp_path_factory.mktemp("wty_ro") / "index.sqlite"
    storage.create_index(db)
    storage.bulk_insert(db, WTY_RO, keys=KEYS)
    opened = storage.open_readonly(db)
    yield opened
    opened.close()


@pytest.fixture(scope="module")
def forms(conn):
    """``FormLookup`` as the provider answers it: the read the parser's pass is handed."""
    return lambda terms: storage.term_rows(conn, terms, keys=KEYS)


@pytest.fixture(scope="module")
def tagger():
    from anki_miner.languages.ro.tokenizer import build_tagger

    return build_tagger()


def _injected(monkeypatch):
    seen: dict[str, object] = {}

    def fake(config, **kwargs):
        seen.update(kwargs)
        return "parser"

    monkeypatch.setattr("anki_miner.languages._spaced.create_spaced_parser", fake)
    assert get_profile("ro").create_parser(AnkiMinerConfig()) == "parser"
    return seen["token_post_pass"]


def _fronts(monkeypatch, tagger, forms, line: str) -> dict[str, tuple[str, str]]:
    tokens = tagger(line)
    _injected(monkeypatch)(tokens, None, forms)
    return {token.surface: (token.feature.lemma, token.feature.pos1) for token in tokens}


def test_the_romanian_keys_declare_the_reading_column():
    assert isinstance(KEYS, CasefoldDictKeys) and KEYS.term_rows_match_reading is True


def test_a_diacritic_surface_reaches_its_form_row_through_the_reading(conn, forms):
    """``lasă`` is no term; the ``lasa`` row spelt ``lasă`` answers, and not the one spelt ``lașă``."""
    assert forms(["lasă", "Lasă"]) == {
        "lasă": [(WTY_RO[0].content, "non-lemma")],
        "Lasă": [(WTY_RO[0].content, "non-lemma")],
    }
    exact_term_keys = CasefoldDictKeys(extra_fold=ro_fold_cedilla)
    assert storage.term_rows(conn, ["lasă"], keys=exact_term_keys) == {}


def test_the_parser_runs_the_homograph_the_content_front_the_non_headword_and_the_propn_passes_in_order(monkeypatch):
    injected = _injected(monkeypatch)
    assert isinstance(injected, OrderedPasses)
    homograph, content, non_headword, propn = injected._passes  # noqa: SLF001 - the order is the contract
    assert isinstance(homograph, RomanianHomographPass) and isinstance(propn, RomanianPropnRecovery)
    assert isinstance(non_headword, RomanianNonHeadwordFront)
    assert isinstance(content, FormOfLemmaPass) and content._front_pos == frozenset(UPOS_ALLOWED)  # noqa: SLF001
    assert content._row_targets is ro_row_targets  # noqa: SLF001


@pytest.mark.parametrize(
    ("line", "surface", "front"),
    [
        ("Lasă-mă în pace!", "Lasă", "lăsa"),
        ("Mă scuzați, caut strada principală.", "caut", "căuta"),
        ("Nu te mai plânge și mănâncă-ți ciorba.", "mănâncă", "mânca"),
        ("Crede-mă, nu mint.", "mint", "minți"),
        ("Într-o zi o să înțelegi.", "înțelegi", "înțelege"),
    ],
)
def test_real_verb_fronts(monkeypatch, tagger, forms, line, surface, front):
    assert _fronts(monkeypatch, tagger, forms, line)[surface] == (front, "VERB")


def test_a_headword_keeps_its_front(monkeypatch, tagger, forms):
    assert _fronts(monkeypatch, tagger, forms, "Lasă-mă în pace!")["pace"] == ("pace", "NOUN")


def test_a_noun_target_makes_the_front(monkeypatch, forms):
    """Any content lemma row makes a front: ``cărți``'s form row names the noun ``carte``."""
    token = LanguageToken(surface="cărți", pos1="NOUN", lemma="cărț")
    _injected(monkeypatch)([token], None, forms)
    assert (token.feature.lemma, token.feature.pos1) == ("carte", "NOUN")


@pytest.mark.parametrize(
    ("line", "surface", "front"),
    [
        ("Verificăm gările și aeroportul.", "gările", ("gară", "NOUN")),
        ("Și tu, tată.", "tată", ("tată", "NOUN")),  # tagged VERB ``tat``
        ("Unde ai fost aseară?", "aseară", ("aseară", "ADV")),  # tagged VERB ``asear``
    ],
)
def test_real_noun_and_adverb_fronts(monkeypatch, tagger, forms, line, surface, front):
    assert _fronts(monkeypatch, tagger, forms, line)[surface] == front


def test_a_plural_fronts_its_singular(monkeypatch, tagger, forms):
    """``cheie``'s first lemma row is its adjective sense, so the front's class is ADJ (a known residual)."""
    assert _fronts(monkeypatch, tagger, forms, "Ai văzut cheile mele?")["cheile"][0] == "cheie"


@pytest.mark.parametrize(
    ("line", "surface", "front"),
    [
        ("Pot să plătesc cu cardul?", "Pot", "putea"),  # ``pot`` is a noun headword (pot)
        ("Am uitat să sun la bunica.", "sun", "suna"),  # ``sun`` is an obsolete noun headword
    ],
)
def test_a_verb_lemmatised_to_a_noun_headword_takes_the_verb_its_surface_names(
    monkeypatch, tagger, forms, line, surface, front
):
    assert _fronts(monkeypatch, tagger, forms, line)[surface] == (front, "VERB")


def test_a_verb_whose_surface_names_several_lemmas_keeps_its_front(monkeypatch, forms):
    """``vii``'s form rows name ``vie``, ``viu`` and ``veni``: the pass never picks the verb among them."""
    token = LanguageToken(surface="vii", pos1="VERB", lemma="viu")
    _injected(monkeypatch)([token], None, forms)
    assert (token.feature.lemma, token.feature.pos1) == ("viu", "VERB")


def test_a_verb_headword_lemma_is_untouched(monkeypatch, tagger, forms):
    """``vine`` also names the noun ``vână``; its lemma ``veni`` is a verb headword, so nothing reads it."""
    assert _fronts(monkeypatch, tagger, forms, "Mi-a spus că vine mâine.")["vine"] == ("veni", "VERB")


def test_a_cue_initial_verb_tagged_propn_is_recovered(monkeypatch, tagger, forms):
    assert _fronts(monkeypatch, tagger, forms, "Aștept, nu mă grăbesc.")["Aștept"] == ("aștepta", "VERB")


def test_a_mid_line_capitalised_propn_stays_a_name(monkeypatch, tagger, forms):
    """``Viorel`` has only the adjective row ``viorel``; a capital mid-line is a name, whatever the rows say."""
    fronts = _fronts(monkeypatch, tagger, forms, "Costel, Ionel și Viorel sunt verișori.")
    assert fronts["Viorel"] == ("Viorel", "PROPN") and fronts["Ionel"] == ("Ionel", "PROPN")


def test_a_cue_initial_name_with_a_name_row_stays_a_name(monkeypatch, tagger, forms):
    """``ion`` is a noun (ion) as well as the name ``Ion``: its name row keeps the token PROPN."""
    assert _fronts(monkeypatch, tagger, forms, "Ion a venit acasă.")["Ion"] == ("Ion", "PROPN")


def _front(monkeypatch, forms, surface: str, pos1: str, lemma: str) -> tuple[str, str]:
    """One token as ``ro_core_news_sm`` tagged it on a real subtitle line, through the parser's passes."""
    token = LanguageToken(surface=surface, pos1=pos1, lemma=lemma)
    _injected(monkeypatch)([token], None, forms)
    return token.feature.lemma, token.feature.pos1


def test_an_inflection_gloss_reads_as_the_form_row_it_is(conn):
    rows = storage.term_rows(conn, ["vorbim", "fii", "mită"], keys=KEYS)
    read = {key: [ro_row_targets(content, tags) for content, tags in found] for key, found in rows.items()}
    assert read["vorbim"] == [["vorbi"], ["vorbi"]]
    assert read["fii"] == [["fiu"], ["fi"], ["fi", "fi"], ["fie", "fie", "fie"], ["fiu", "fiu"]]
    assert read["mită"] == [None]


@pytest.mark.parametrize(
    ("surface", "lemma", "front"),
    [
        ("vorbim", "vorbim", "vorbi"),  # ``Vorbim mâine.``
        ("vorbești", "vorbest", "vorbi"),  # ``Ce vorbești acolo?``
        ("fii", "fii", "fi"),  # ``Fii atent!``: the glosses name ``fiu`` and ``fi``, the verb is ``fi``
    ],
)
def test_a_form_kaikki_files_as_a_headword_fronts_its_lemma(monkeypatch, forms, surface, lemma, front):
    assert _front(monkeypatch, forms, surface, "VERB", lemma) == (front, "VERB")


@pytest.mark.parametrize(
    ("surface", "pos1", "lemma", "front"),
    [
        ("scapi", "VERB", "scapi", ("scăpa", "VERB")),  # ``să scapi de el``: ``scap`` is a noun
        ("ții", "VERB", "ția", ("ține", "VERB")),  # ``Te ții de școală.``: ``ținea`` is no headword
        ("uite", "VERB", "ui", ("uita", "VERB")),  # ``Ia uite!``: the surface's interjection row is no front
        ("sucești", "VERB", "sucesc", ("suci", "VERB")),  # the surface is also a village
        ("prostule", "NOUN", "prostu", ("prost", "ADJ")),  # the lemma is only a surname
    ],
)
def test_a_lemma_no_word_files_takes_the_target_of_its_class(monkeypatch, forms, surface, pos1, lemma, front):
    assert _front(monkeypatch, forms, surface, pos1, lemma) == front


def test_two_targets_of_the_token_s_class_keep_the_token(monkeypatch, forms):
    """``vina`` names the verbs ``veni`` and ``vâna`` (and two nouns): nothing tells them apart."""
    assert _front(monkeypatch, forms, "vina", "VERB", "vina") == ("vina", "VERB")


def test_a_noun_lemmatised_to_an_adverb_headword_takes_the_noun_its_surface_names(monkeypatch, forms):
    """``toată noaptea``: wty-ro-en files ``noaptea`` only as the adverb "at night"."""
    assert _front(monkeypatch, forms, "noaptea", "NOUN", "noaptea") == ("noapte", "NOUN")


def test_the_tagger_s_own_lemma_counts_among_the_surface_s_targets(monkeypatch, forms):
    """``Taci!`` names ``tacă`` (silence) and ``tăcea``: the tagger's ``tacă`` is one of them, so nothing picks.

    Not counting it fronted ``tăcea`` here, but the same reading made ``dragă domnule`` ``draga`` and ``ați atins
    limita`` the verb ``limita``.
    """
    assert _front(monkeypatch, forms, "taci", "VERB", "tacă") == ("tacă", "VERB")


def test_a_closed_class_lemma_is_left_to_its_word():
    """``peste zece ani`` tagged ADV keeps the preposition: its form row names the noun ``pestă``."""
    rows = {"peste": [("<div>peste</div>", "prep"), (_form_of("peste", None, "pestă").content, "non-lemma")]}
    rows["pestă"] = [("<div>pestă</div>", "n fem")]
    token = LanguageToken(surface="peste", pos1="ADV", lemma="peste")
    RomanianNonHeadwordFront()([token], None, lambda keys: {key: rows[key] for key in keys if key in rows})
    assert (token.feature.lemma, token.feature.pos1) == ("peste", "ADV")


def test_no_dictionary_leaves_the_tokens_as_the_tagger_built_them(monkeypatch, tagger):
    tokens = tagger("Lasă-mă în pace!")
    before = [(t.surface, t.feature.lemma, t.feature.pos1) for t in tokens]
    _injected(monkeypatch)(tokens, None, None)
    assert [(t.surface, t.feature.lemma, t.feature.pos1) for t in tokens] == before


# --------------------------------------------------------------------------
# ROM-07: „ opens a quotation, so Reading keeps a quoted sentence whole
# --------------------------------------------------------------------------

RULES = get_profile("ro").sentence_rules


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        (
            "„Am uitat pâinea! Mă întorc imediat.” Mama a zâmbit. „Nu-i nimic.”",
            ["„Am uitat pâinea! Mă întorc imediat.” Mama a zâmbit.", "„Nu-i nimic.”"],
        ),
        ("El a spus: „Vin mâine. Sigur.” Apoi a plecat.", ["El a spus: „Vin mâine. Sigur.” Apoi a plecat."]),
        ("Dl. Popescu a venit. Bine.", ["Dl. Popescu a venit.", "Bine."]),
    ],
)
def test_romanian_quotes_keep_a_quoted_sentence_whole(text, sentences):
    assert split_sentences(text, rules=RULES) == sentences


def test_the_opener_is_added_to_the_shared_latin_rules():
    base = sentence_rules(RO_ABBREVIATIONS)
    assert base.openers < RULES.openers and RULES.openers - base.openers == {"„"}
    assert "”" in RULES.closers and "«" in RULES.openers  # the inner «…» pair was already shared
    assert dataclasses.replace(base, openers=RULES.openers) == RULES

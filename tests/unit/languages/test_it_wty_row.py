"""Real wty-it-en rows survive import + render under the Italian keys; the noun hook reads them; the lo rule has no tag."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "it" / "wty_row.json").read_text(encoding="utf-8"))
NOUNS = {
    "zio": ("masculine", "lo"),
    "psicologo": ("masculine", "lo"),
    "gatto": ("masculine", "il"),
    "sedia": ("feminine", "la"),
    "albero": ("masculine", "l'"),
    "acqua": ("feminine", "l'"),
    "giornale": ("masculine", "il"),
}


@pytest.fixture
def provider(tmp_path):
    archive = tmp_path / "wty-it-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-it-en", language="it")
    loaded = IndexedDictProvider(
        "wty-it-en", tmp_path / "dicts" / "wty-it-en" / "index.sqlite", keys=get_profile("it").dict_keys
    )
    assert loaded.load()
    return loaded


def test_the_fixture_carries_its_licence():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "it"


@pytest.mark.parametrize("term", sorted(FIXTURE["rendered_html"]))
def test_every_row_renders_the_committed_html(provider, term):
    assert provider.lookup(term) == FIXTURE["rendered_html"][term]


def test_a_capitalised_query_meets_the_casefolded_key(provider):
    assert provider.lookup("Zio") == FIXTURE["rendered_html"]["zio"]


def _grammar(term: str, html: str | None, morph: str = "", surface: str | None = None) -> dict[str, str]:
    hook = get_profile("it").render_hooks[1]
    word = SimpleNamespace(
        pos="NOUN", mined_form=term, surface=surface or term, morph=morph, definition_html=html or ""
    )
    return hook.render(word, config=AnkiMinerConfig())


@pytest.mark.parametrize(("term", "expected"), sorted(NOUNS.items()))
def test_the_grammar_hook_reads_the_real_rows(provider, term, expected):
    gender, article = expected
    assert _grammar(term, provider.lookup(term)) == {"noun_gender": gender, "noun_article": article}


def test_a_lemma_that_flipped_gender_takes_the_front_entry_chip(provider):
    """psicologa -> lemma psicologo keeps Gender=Fem on the token; the one masc chip wins (contract item 9)."""
    html = provider.lookup("psicologo")
    assert _grammar("psicologo", html, morph="Gender=Fem|Number=Sing", surface="psicologa") == {
        "noun_gender": "masculine",
        "noun_article": "lo",
    }


def test_a_noun_of_either_gender_gets_no_field_without_context(provider):
    assert _grammar("artista", provider.lookup("artista")) == {}


def test_no_noun_row_carries_an_article_tag_and_the_lo_row_states_the_rule():
    noun_tags = {tag for row in FIXTURE["term_rows"] if row[2].split()[:1] == ["n"] for tag in row[2].split()}
    assert noun_tags <= {"n", "masc", "fem"}
    lo_gloss = FIXTURE["rendered_html"]["lo"]
    assert re.search(r"s\+consonant.*gn, pn, ps, x, y, or z", lo_gloss)

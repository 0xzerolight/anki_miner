"""Real wty-el-en rows survive import + render under the Greek keys; the gender hook prints the article."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "el" / "wty_row.json").read_text(encoding="utf-8"))
NOUNS = {"βιβλίο": "το", "σπίτι": "το", "δρόμος": "ο", "οδός": "η", "γάτα": "η"}


@pytest.fixture
def provider(tmp_path):
    archive = tmp_path / "wty-el-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-el-en", language="el")
    loaded = IndexedDictProvider(
        "wty-el-en", tmp_path / "dicts" / "wty-el-en" / "index.sqlite", keys=get_profile("el").dict_keys
    )
    assert loaded.load()
    return loaded


def test_the_fixture_carries_its_licence():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "el"


@pytest.mark.parametrize("term", sorted(FIXTURE["rendered_html"]))
def test_every_row_renders_the_committed_html(provider, term):
    assert provider.lookup(term) == FIXTURE["rendered_html"][term]


def test_the_head_line_renders_byte_exact(provider):
    assert 'data-sc-content="Grammar-content">βιβλίο • (vivlío) n (plural βιβλία)</div>' in provider.lookup("βιβλίο")


@pytest.mark.parametrize("query", ["Οδός", "ΟΔΌΣ", "οδόσ", "οδ\u1f79ς"])
def test_case_final_sigma_and_oxia_meet_the_key(provider, query):
    assert provider.lookup(query) == FIXTURE["rendered_html"]["οδός"]


def test_an_unaccented_capital_query_is_the_settled_miss(provider):
    """R34: keys keep the tonos and there is no accent-stripped rung."""
    assert provider.lookup("ΟΔΟΣ") is None


@pytest.mark.parametrize(("form", "lemma"), [("έγραψα", "γράφω"), ("βιβλία", "βιβλίο")])
def test_a_form_row_names_its_lemma(provider, form, lemma):
    """How the PROBE reaches γράφω: the form-of row renders its base form."""
    html = provider.lookup(form)
    assert html is not None and lemma in html


def _gender(term: str, html: str | None, morph: str = "", pos: str = "NOUN") -> dict[str, str]:
    hook = get_profile("el").render_hooks[1]
    word = SimpleNamespace(pos=pos, mined_form=term, surface=term, morph=morph, definition_html=html or "")
    return hook.render(word, config=AnkiMinerConfig())


@pytest.mark.parametrize(("term", "article"), sorted(NOUNS.items()))
def test_the_gender_hook_prints_the_article_from_the_real_rows(provider, term, article):
    assert _gender(term, provider.lookup(term)) == {"noun_gender": article}


def test_the_model_gender_leads_unless_the_dictionary_rules_it_out(provider):
    html = provider.lookup("βιβλίο")
    assert _gender("βιβλίο", html, morph="Case=Nom|Gender=Neut|Number=Sing") == {"noun_gender": "το"}
    assert _gender("βιβλίο", html, morph="Gender=Masc") == {"noun_gender": "το"}
    assert _gender("βιβλίο", None, morph="Gender=Fem") == {"noun_gender": "η"}
    assert _gender("γράφω", provider.lookup("γράφω"), pos="VERB") == {}


def test_the_head_line_rung_is_inert_for_greek():
    """The romanisation sits between the headword and the gender letter: ``βιβλίο • (vivlío) n``."""
    html = '<li data-dictionary="wty-el-en"><div data-sc-content="Grammar-content">βιβλίο • (vivlío) n (plural βιβλία)</div></li>'
    assert _gender("βιβλίο", html) == {}

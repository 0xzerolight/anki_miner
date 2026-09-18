"""Real wty-lt-en rows survive import + render under the Lithuanian keys; the gender hook reads the head line."""

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

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "lt" / "wty_row.json").read_text(encoding="utf-8"))
NOUNS = {"knyga": "feminine", "stalas": "masculine", "žmogus": "masculine"}


@pytest.fixture
def provider(tmp_path):
    archive = tmp_path / "wty-lt-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-lt-en", language="lt")
    loaded = IndexedDictProvider(
        "wty-lt-en", tmp_path / "dicts" / "wty-lt-en" / "index.sqlite", keys=get_profile("lt").dict_keys
    )
    assert loaded.load()
    return loaded


def test_the_fixture_carries_its_licence():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "lt"


@pytest.mark.parametrize("term", sorted(FIXTURE["rendered_html"]))
def test_every_row_renders_the_committed_html(provider, term):
    assert provider.lookup(term) == FIXTURE["rendered_html"][term]


@pytest.mark.parametrize("query", ["Knyga", "KNYGA", "kny\u0301ga", "kny\u0303ga"])
def test_case_and_stress_meet_the_same_key(provider, query):
    """D-1 at the index: the stress fold runs on the key AND the query, so a stressed row finds the plain one."""
    assert provider.lookup(query) == FIXTURE["rendered_html"]["knyga"]


def test_the_stressed_inflected_row_is_reachable_without_its_marks(provider):
    """112,100 of the 188,425 form rows are keyed WITH stress; the fold is what makes them findable."""
    assert provider.lookup("knygos") is not None
    assert provider.lookup("knyga\u0300") == provider.lookup("knyga")


def test_a_form_row_names_its_lemma(provider):
    html = provider.lookup("knygos")
    assert html is not None and "knyga" in html


def _gender(term: str, html: str | None, morph: str = "", pos: str = "NOUN") -> dict[str, str]:
    hook = get_profile("lt").render_hooks[1]
    word = SimpleNamespace(pos=pos, mined_form=term, surface=term, morph=morph, definition_html=html or "")
    return hook.render(word, config=AnkiMinerConfig())


@pytest.mark.parametrize(("term", "label"), sorted(NOUNS.items()))
def test_the_gender_hook_reads_the_head_line_of_the_real_rows(provider, term, label):
    """83.9 % of noun rows carry the gender letter; the repeated head-line copy is harmless (D-6)."""
    assert _gender(term, provider.lookup(term)) == {"noun_gender": label}


def test_the_head_line_stress_marks_are_pre_folded_by_the_shared_helper(provider):
    """D2 (landed): ``knygà f`` reaches the hook as ``knyga f``, so the gender letter is found."""
    html = '<li data-dictionary="wty-lt-en"><div data-sc-content="Grammar-content">knyga\u0300 f (plural knygos)</div></li>'
    assert _gender("knyga", html) == {"noun_gender": "feminine"}


def test_the_model_gender_leads_and_a_verb_prints_nothing(provider):
    assert _gender("knyga", provider.lookup("knyga"), morph="Case=Nom|Gender=Fem|Number=Sing") == {
        "noun_gender": "feminine"
    }
    assert _gender("skaityti", provider.lookup("knyga"), pos="VERB") == {}

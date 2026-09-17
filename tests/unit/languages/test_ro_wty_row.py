"""Real wty-ro-en rows survive import + render under the Romanian keys, and the gender hook reads them."""

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

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "ro" / "wty_row.json").read_text(encoding="utf-8"))
_CHIP = re.compile(r'<span class="gloss-tag"[^>]*>[^<]*</span>')
STIINTA = "știință"
CARTI = "cărți"
CEDILLA_STIINTA = "\u015ftiin\u0163ă"  # \u015ftiin\u0163ă


def _provider(tmp_path: Path, dict_id: str, term_rows: list) -> IndexedDictProvider:
    archive = tmp_path / f"{dict_id}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps({**FIXTURE["index"], "title": dict_id}))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(term_rows))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id=dict_id, language="ro")
    loaded = IndexedDictProvider(
        dict_id, tmp_path / "dicts" / dict_id / "index.sqlite", keys=get_profile("ro").dict_keys
    )
    assert loaded.load()
    return loaded


@pytest.fixture
def provider(tmp_path):
    loaded = _provider(tmp_path, "wty-ro-en", FIXTURE["term_rows"])
    yield loaded
    loaded.close()


def _gender(definition_html: str, morph: str = "") -> dict[str, str]:
    hook = get_profile("ro").render_hooks[1]
    word = SimpleNamespace(pos="NOUN", morph=morph, definition_html=definition_html, mined_form="")
    return hook.render(word, config=AnkiMinerConfig())


def test_the_fixture_carries_its_attribution():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "ro"


@pytest.mark.parametrize("word", ["carte", "scaun", STIINTA])
def test_a_capitalised_query_renders_the_committed_html(provider, word):
    assert provider.lookup(word.capitalize()) == FIXTURE["rendered_html"][word]


def test_a_cedilla_query_meets_the_comma_below_key(provider):
    """R35: the key fold maps cedillas at query time as it did at import, so no ladder rung exists."""
    assert provider.lookup(CEDILLA_STIINTA) == FIXTURE["rendered_html"][STIINTA]


def test_the_probe_word_reaches_its_lemma_through_the_form_of_rows_reading(provider):
    """R13: wty-ro-en keys form-of rows without diacritics; the real spelling is the reading column."""
    (form_of,) = [row for row in FIXTURE["term_rows"] if row[2] == "non-lemma"]
    assert (form_of[0], form_of[1]) == ("carti", CARTI)
    html = provider.lookup(CARTI)
    assert html == FIXTURE["rendered_html"][CARTI] and "carte" in html
    assert provider.lookup(CARTI.capitalize()) == html


@pytest.mark.parametrize("word", ["carte", "scaun", STIINTA])
def test_the_gender_chip_gives_the_label(word):
    assert _gender(FIXTURE["rendered_html"][word]) == {"noun_gender": FIXTURE["expected_gender"][word]}


@pytest.mark.parametrize("word", ["carte", "scaun", STIINTA])
def test_the_head_line_alone_gives_the_same_label(word):
    without_chips = _CHIP.sub("", FIXTURE["rendered_html"][word])
    assert "Grammar-content" in without_chips
    assert _gender(without_chips) == {"noun_gender": FIXTURE["expected_gender"][word]}


def test_the_dictionary_neuter_leads_the_models_agreement_gender():
    """E.2.5: UD RRT has no neuter, so the model tags scaun Masc and scaunele Fem; the dictionary decides."""
    html = FIXTURE["rendered_html"]["scaun"]
    assert _gender(html, morph="Case=Acc,Nom|Definite=Def|Gender=Fem|Number=Plur") == {"noun_gender": "neuter"}
    assert _gender(_CHIP.sub("", html), morph="Definite=Ind|Gender=Masc|Number=Sing") == {"noun_gender": "neuter"}


def test_the_morph_gender_is_the_last_resort():
    stub = FIXTURE["rendered_html"][CARTI]  # a form-of stub: a non-lemma chip, no gender chip, no head line
    assert "Grammar-content" not in stub and 'data-category="gender-' not in stub
    assert _gender(stub, morph="Case=Acc,Nom|Definite=Ind|Gender=Fem|Number=Plur") == {"noun_gender": "feminine"}
    assert _gender(stub) == {}

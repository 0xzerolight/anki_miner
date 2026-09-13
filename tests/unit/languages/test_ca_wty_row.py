"""Real wty-ca-en rows survive import + render under the Catalan keys, and the gender hook reads them."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "ca" / "wty_row.json").read_text(encoding="utf-8"))
_CHIP = re.compile(r'<span class="gloss-tag"[^>]*>[^<]*</span>')


def _provider(tmp_path: Path, dict_id: str, term_rows: list) -> IndexedDictProvider:
    archive = tmp_path / f"{dict_id}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps({**FIXTURE["index"], "title": dict_id}))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(term_rows))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id=dict_id, language="ca")
    loaded = IndexedDictProvider(
        dict_id, tmp_path / "dicts" / dict_id / "index.sqlite", keys=get_profile("ca").dict_keys
    )
    assert loaded.load()
    return loaded


@pytest.fixture
def provider(tmp_path):
    loaded = _provider(tmp_path, "wty-ca-en", FIXTURE["term_rows"])
    yield loaded
    loaded.close()


def _gender(definition_html: str, morph: str = "") -> dict[str, str]:
    hook = get_profile("ca").render_hooks[1]
    word = SimpleNamespace(pos="NOUN", morph=morph, definition_html=definition_html, mined_form="")
    return hook.render(word, config=AnkiMinerConfig())


def test_the_fixture_carries_both_licences():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and "GPL-3.0" in FIXTURE["_comment"]
    assert FIXTURE["index"]["sourceLanguage"] == "ca"


@pytest.mark.parametrize("word", ["llibre", "taula"])
def test_a_capitalised_query_renders_the_committed_html(provider, word):
    assert provider.lookup(word.capitalize()) == FIXTURE["rendered_html"][word]


@pytest.mark.parametrize("word", ["llibre", "taula"])
def test_the_gender_chip_gives_the_article_label(word):
    assert _gender(FIXTURE["rendered_html"][word]) == {"noun_gender": FIXTURE["expected_gender"][word]}


@pytest.mark.parametrize("word", ["llibre", "taula"])
def test_the_head_line_alone_gives_the_same_label(word):
    without_chips = _CHIP.sub("", FIXTURE["rendered_html"][word])
    assert "Grammar-content" in without_chips
    assert _gender(without_chips) == {"noun_gender": FIXTURE["expected_gender"][word]}


def test_a_chip_that_excludes_the_morph_gender_wins():
    """en contract item 9: morph Fem against the one masculine chip of llibre -> the chip (CA-1)."""
    assert _gender(FIXTURE["rendered_html"]["llibre"], morph="Gender=Fem|Number=Sing") == {"noun_gender": "el"}


def test_the_morph_gender_leads_when_the_block_has_no_chips():
    without_chips = _CHIP.sub("", FIXTURE["rendered_html"]["llibre"])
    assert _gender(without_chips, morph="Gender=Fem|Number=Sing") == {"noun_gender": "la"}


def test_a_morph_gender_the_chips_include_leads():
    assert _gender(FIXTURE["rendered_html"]["taula"], morph="Gender=Fem|Number=Plur") == {"noun_gender": "la"}


def test_the_fallback_reaches_the_full_entry_through_the_lemma_before_the_surface_stub(tmp_path):
    """CA-2: mined colegi from the surface colegis; wty keys col·legi (full) and col·legis (a form-of stub only)."""
    rows = [
        ["col·legi", "", "n masc", "", 0, ["school"], 0, ""],
        ["col·legis", "", "non-lemma", "", 0, [["col·legi", ["plural"]]], 0, ""],
    ]
    degraded = _provider(tmp_path, "degraded", rows)
    try:
        service = DefinitionService(
            switch_language(AnkiMinerConfig(), "ca"), [degraded], lookup=get_profile("ca").lookup
        )
        (html,) = service.get_definitions_batch([("colegi", None)], fallback_context={"colegi": ("colegis", None)})
    finally:
        degraded.close()
    assert html is not None and "school" in html  # the stub alone renders only "col·legi"

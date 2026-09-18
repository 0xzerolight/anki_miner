"""Real wty-pl-en rows survive import + render under the Polish keys, and the gender hook reads them."""

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

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "pl" / "wty_row.json").read_text(encoding="utf-8"))
_CHIP = re.compile(r'<span class="gloss-tag"[^>]*>[^<]*</span>')
#: The head line gives these; the chips agree (plan P8, Addendum A).
GENDERS = {"książka": "f", "stół": "m inan", "student": "m pers", "pies": "m anim"}


def _provider(tmp_path: Path, dict_id: str, term_rows: list) -> IndexedDictProvider:
    archive = tmp_path / f"{dict_id}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps({**FIXTURE["index"], "title": dict_id}))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(term_rows))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id=dict_id, language="pl")
    loaded = IndexedDictProvider(
        dict_id, tmp_path / "dicts" / dict_id / "index.sqlite", keys=get_profile("pl").dict_keys
    )
    assert loaded.load()
    return loaded


@pytest.fixture
def provider(tmp_path):
    loaded = _provider(tmp_path, "wty-pl-en", FIXTURE["term_rows"])
    yield loaded
    loaded.close()


def _gender(definition_html: str, morph: str = "") -> dict[str, str]:
    hook = get_profile("pl").render_hooks[1]
    word = SimpleNamespace(pos="NOUN", morph=morph, definition_html=definition_html, mined_form="")
    return hook.render(word, config=AnkiMinerConfig())


def test_the_fixture_carries_its_attribution():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "pl"


@pytest.mark.parametrize("word", sorted(GENDERS))
def test_a_capitalised_query_renders_the_committed_html(provider, word):
    """S4: CasefoldDictKeys, so a sentence-initial Książki reaches the książka row."""
    assert provider.lookup(word.capitalize()) == FIXTURE["rendered_html"][word]


def test_the_probe_word_reaches_its_row(provider):
    """PROBE["pl"] is książki: the lookup ladder probes the lemma first, then the surface."""
    assert provider.lookup("książka") == FIXTURE["rendered_html"]["książka"]
    assert get_profile("pl").lookup.candidates("książka", "Książki", None)[0] == ("Książki", 0)


@pytest.mark.parametrize("word", sorted(GENDERS))
def test_the_gender_chip_gives_the_label(word):
    assert _gender(FIXTURE["rendered_html"][word]) == {"noun_gender": GENDERS[word]}


@pytest.mark.parametrize("word", sorted(GENDERS))
def test_the_head_line_alone_gives_the_same_label(word):
    """Addendum A: the head-line parse skips ONE trailing animacy qualifier (stół m inan)."""
    without_chips = _CHIP.sub("", FIXTURE["rendered_html"][word])
    assert "Grammar-content" in without_chips
    assert _gender(without_chips) == {"noun_gender": GENDERS[word]}


def test_the_morph_animacy_is_the_last_resort():
    """No head line and no chips: the model's Animacy=Hum|Nhum|Inan names the sub-gender."""
    assert _gender("", morph="Animacy=Hum|Case=Nom|Gender=Masc|Number=Sing") == {"noun_gender": "m pers"}
    assert _gender("", morph="Animacy=Inan|Case=Loc|Gender=Masc|Number=Sing") == {"noun_gender": "m inan"}
    assert _gender("", morph="Case=Nom|Gender=Fem|Number=Sing") == {"noun_gender": "f"}


def test_a_pluralia_tantum_row_prints_no_gender():
    """P3 end to end: drzwi has no gender chip and no head-line gender, and the repair removed Gender=
    from the morph, so the card field stays empty instead of printing the PDB convention n."""
    html = FIXTURE["rendered_html"]["drzwi"]
    assert _gender(html) == {}
    assert _gender(html, morph="Case=Nom|Number=Ptan") == {}
    assert _gender(html, morph="Case=Nom|Gender=Neut|Number=Ptan") == {"noun_gender": "n"}  # the repair's job

"""Greek aspect: the model's Aspect= on the verb as used in the sentence (Ruling S1)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "el" / "wty_row.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def parser():
    return get_profile("el").create_parser(switch_language(AnkiMinerConfig(), "el"))


def _word(parser, sentence: str, front: str):
    units = [ReadingUnit(text=sentence, index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False)
    (word,) = [w for w in words if w.mined_form == front]
    return word


def _render(word, html: str = "") -> dict[str, str]:
    word.definition_html = html
    return get_profile("el").render_hooks[1].render(word, config=AnkiMinerConfig())


@pytest.mark.parametrize(
    ("sentence", "front", "feature", "aspect"),
    [
        ("Έγραψα ένα γράμμα στη μητέρα μου χθες.", "έγραψα", "Aspect=Perf", "perfective"),
        ("Δεν ξέρω τι να κάνω.", "ξέρω", "Aspect=Imp", "imperfective"),
    ],
)
def test_the_aspect_of_the_form_as_used(parser, sentence, front, feature, aspect):
    word = _word(parser, sentence, front)
    assert word.pos == "VERB" and feature in word.morph.split("|")
    assert _render(word) == {"aspect_pair": aspect}


def test_the_greek_head_line_names_no_aspect_partner(parser, tmp_path):
    """γράφω • (gráfo) (past έγραψα, passive γράφομαι): a past stem, not a "perfective X" partner."""
    archive = tmp_path / "wty-el-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-el-en", language="el")
    provider = IndexedDictProvider(
        "wty-el-en", tmp_path / "dicts" / "wty-el-en" / "index.sqlite", keys=get_profile("el").dict_keys
    )
    assert provider.load()
    word = _word(parser, "Έγραψα ένα γράμμα στη μητέρα μου χθες.", "έγραψα")
    assert _render(word, provider.lookup("γράφω") or "") == {"aspect_pair": "perfective"}


def test_a_noun_gets_gender_and_no_aspect(parser):
    word = _word(parser, "Το βιβλίο είναι στο σπίτι.", "βιβλίο")
    assert _render(word) == {"noun_gender": "το"}

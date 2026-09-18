"""Finnish data: the POS gate, the fine-tag exclusion against the model's own tagset, abbreviations, normalise."""

from __future__ import annotations

import importlib
import json
import unicodedata
from pathlib import Path

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages.fi.morphology import (
    FI_ABBREVIATIONS,
    FI_ALLOWED_POS,
    FI_EXCLUDED_SUBTYPES,
    FI_MODEL_PACKAGE,
    fi_normalize,
)

#: ``labels.tagger`` of fi_core_news_sm 3.8.0: the TDT fine tagset (E.1 "17 fine tags").
TDT_TAGS = frozenset(
    ("A", "Adj", "Adp", "Adv", "Adv_V", "C", "C_V", "Foreign", "Interj", "N", "Num", "Pron", "Punct", "Symb", "V",
     "V_Pron", "_SP")
)  # fmt: skip


def test_the_gate_is_the_shared_content_classes():
    assert FI_MODEL_PACKAGE == "fi_core_news_sm"
    assert FI_ALLOWED_POS == UPOS_ALLOWED


def test_the_exclusion_is_part_of_the_models_tagset_and_spares_the_open_classes():
    package = importlib.import_module(FI_MODEL_PACKAGE)
    meta_path = Path(package.__file__).parent / "fi_core_news_sm-3.8.0" / "meta.json"
    labels = json.loads(meta_path.read_text(encoding="utf-8"))["labels"]["tagger"]
    assert frozenset(labels) == TDT_TAGS and len(labels) == 17
    assert FI_EXCLUDED_SUBTYPES == ("Adv_V", "C_V")
    assert set(FI_EXCLUDED_SUBTYPES) < TDT_TAGS
    assert not {"N", "V", "A", "Adv", "Num", "Pron", "Foreign", "Punct"} & set(FI_EXCLUDED_SUBTYPES)


def test_abbreviations_are_spacys_finnish_dotted_exceptions():
    from spacy.lang.fi.tokenizer_exceptions import _exc

    dotted = {key[:-1].casefold() for key in _exc if key.endswith(".") and any(char.isalpha() for char in key)}
    assert dotted == FI_ABBREVIATIONS and len(FI_ABBREVIATIONS) == 64
    assert {"esim", "mm", "jne", "ks", "puh.joht"} <= FI_ABBREVIATIONS
    assert all(key == key.casefold() and not key.endswith(".") for key in FI_ABBREVIATIONS)


def test_normalise_composes_and_drops_invisible_breaks_but_keeps_finnish_letters():
    assert fi_normalize(unicodedata.normalize("NFD", "Äiti söi päärynän")) == "Äiti söi päärynän"
    assert fi_normalize("kirja\u00adkauppa") == "kirjakauppa"
    assert fi_normalize("10\u00a0000 euroa") == "10 000 euroa"
    assert fi_normalize("Åland") == "Åland"
    assert fi_normalize(fi_normalize("kirja\u00adkauppa")) == "kirjakauppa"

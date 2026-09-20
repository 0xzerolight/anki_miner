"""The generated Indonesian tables (plan D6, D7): provenance, shape and the rows the tokenizer and ladder rely on."""

from __future__ import annotations

import inspect

import pytest

from anki_miner.languages.id import colloquial, stopwords
from anki_miner.languages.id.colloquial import ID_COLLOQUIAL, ID_COLLOQUIAL_CORE
from anki_miner.languages.id.stopwords import ID_STOPWORDS


def test_the_stopword_tier_keeps_function_words_and_drops_content_words():
    assert len(ID_STOPWORDS) == 532
    assert all(word == word.casefold() and word.isascii() for word in ID_STOPWORDS)
    # the C.5 function-word core: closed classes, monomorphemic (D6)
    assert {"yang", "dan", "ini", "itu", "pada", "di", "ke", "dari", "saya", "dia", "tidak"} <= ID_STOPWORDS
    assert {"akan", "telah", "sedang", "adalah", "ya", "kan", "sih", "para", "seorang"} <= ID_STOPWORDS
    # stopwords-iso is an information-retrieval list: a word with any noun, verb or adjective
    # sense is content vocabulary and must stay mineable, however common (D6)
    assert not {"waktu", "saat", "membuat", "menjadi", "kembali", "tahu", "baik", "baru"} & ID_STOPWORDS
    assert not {"benar", "dapat", "sampai", "sesuatu", "sekarang", "mungkin", "dalam"} & ID_STOPWORDS
    assert not {"bapak", "bekerja", "masalah", "bisa", "datang", "sudah", "mau"} & ID_STOPWORDS
    # the colloquial function words, the particle and the standalone clitic are added (C.5)
    assert {"gue", "lo", "nggak", "gak", "udah", "aja", "kayak", "sih", "deh", "kan", "ya", "nya"} <= ID_STOPWORDS


def test_only_the_curated_core_carries_the_stopword_read_through():
    """IndoCollex is crowd-derived: its formal side never makes a word a stopword (D6).

    ``morphology.is_stopword`` reads :data:`ID_COLLOQUIAL_CORE` alone; the counts here are what that
    decision costs and saves. The whole table would add 332 words nobody reviewed.
    """
    assert len(ID_COLLOQUIAL_CORE) == 28
    assert ID_COLLOQUIAL_CORE.items() <= ID_COLLOQUIAL.items()
    assert len([key for key, formal in ID_COLLOQUIAL_CORE.items() if formal in ID_STOPWORDS]) == 23
    unreviewed = [
        key
        for key, formal in ID_COLLOQUIAL.items()
        if formal in ID_STOPWORDS and key not in ID_STOPWORDS and key not in ID_COLLOQUIAL_CORE
    ]
    assert len(unreviewed) == 332


def test_the_colloquial_table_is_indocollex_under_the_curated_core():
    assert len(ID_COLLOQUIAL) == 1988
    core = {
        "lo": "kamu",
        "nggak": "tidak",
        "gak": "tidak",
        "udah": "sudah",
        "aja": "saja",
        "gimana": "bagaimana",
        "yg": "yang",
        "banget": "sangat",
        "bikin": "membuat",
        "kayak": "seperti",
    }
    assert {key: ID_COLLOQUIAL[key] for key in core} == core
    assert (ID_COLLOQUIAL["beliin"], ID_COLLOQUIAL["ngerti"], ID_COLLOQUIAL["gue"]) == (
        "membelikan",
        "mengerti",
        "saya",
    )
    assert ID_COLLOQUIAL["dirumah"] == "di rumah"  # a formal side may be a phrase
    assert all(key == key.casefold() and key.isascii() and " " not in key for key in ID_COLLOQUIAL)
    with pytest.raises(TypeError):
        ID_COLLOQUIAL["x"] = "y"  # type: ignore[index]


def test_both_tables_record_their_source_and_notice():
    assert "6109494eb4cfebd75c59bd832848374d02f4e907" in inspect.getdoc(stopwords)
    assert "licenses/stopwords-iso/LICENSE" in inspect.getdoc(stopwords)
    assert "df626e812cd5924794fabf40003368a69f4e953c" in inspect.getdoc(colloquial)
    assert "licenses/indocollex/LICENSE" in inspect.getdoc(colloquial)

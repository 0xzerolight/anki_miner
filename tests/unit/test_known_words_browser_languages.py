"""Review Focus 5 for WP-06: a mining-language switch shows and edits only that language's words."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.gui.widgets.dialogs.known_words_dialog import KnownWordsManagerDialog
from anki_miner.languages.registry import config_language
from anki_miner.services.known_word_db import KnownWordDB


def _seed(config: AnkiMinerConfig, user: set[str], anki: set[str]) -> None:
    db = KnownWordDB(resolve_known_words_db_path(config), language=config_language(config))
    db.initialize()
    db.add_words(anki, source="anki")
    db.add_words(user, source="user")


def _open(qtbot, config: AnkiMinerConfig) -> KnownWordsManagerDialog:
    language = config_language(config)
    dlg = KnownWordsManagerDialog(
        KnownWordDB(resolve_known_words_db_path(config), language=language), language=language
    )
    qtbot.addWidget(dlg)
    dlg.source_filter.setCurrentIndex(dlg.source_filter.findData(""))
    return dlg


def test_the_browser_lists_only_the_active_languages_words(qtbot, tmp_path):
    ja = replace(AnkiMinerConfig(), language="ja", known_words_db_path=tmp_path / "known_words.db")
    es = replace(ja, language="es")
    _seed(ja, user={"猫"}, anki={"犬"})
    _seed(es, user={"gato"}, anki={"perro"})

    assert resolve_known_words_db_path(ja) != resolve_known_words_db_path(es)
    assert sorted(row.lemma for row in _open(qtbot, ja).visible_rows()) == ["犬", "猫"]
    assert sorted(row.lemma for row in _open(qtbot, es).visible_rows()) == ["gato", "perro"]


def test_removing_a_word_in_one_language_leaves_the_other_alone(qtbot, tmp_path):
    ja = replace(AnkiMinerConfig(), language="ja", known_words_db_path=tmp_path / "known_words.db")
    es = replace(ja, language="es")
    _seed(ja, user={"猫"}, anki=set())
    _seed(es, user={"gato"}, anki=set())

    dlg = _open(qtbot, es)
    proxy = dlg.word_table.model()
    dlg.word_table.selectRow(next(r for r in range(proxy.rowCount()) if proxy.index(r, 0).data() == "gato"))
    dlg._on_remove()

    assert KnownWordDB(resolve_known_words_db_path(es), language="es").get_words_by_source("user") == set()
    assert KnownWordDB(resolve_known_words_db_path(ja), language="ja").get_words_by_source("user") == {"猫"}

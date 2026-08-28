"""ja keeps known_words.db verbatim; other languages get a sibling file."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from anki_miner.config import create_default_config
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path


def test_ja_path_is_the_configured_path_verbatim(tmp_path: Path):
    configured = tmp_path / "known_words.db"
    config = dataclasses.replace(create_default_config(), known_words_db_path=configured, language="ja")
    assert resolve_known_words_db_path(config) == configured


def test_non_ja_gets_a_language_sibling(tmp_path: Path):
    configured = tmp_path / "known_words.db"
    for code, expected in (("zh", "known_words.zh.db"), ("ko", "known_words.ko.db")):
        config = dataclasses.replace(create_default_config(), known_words_db_path=configured, language=code)
        resolved = resolve_known_words_db_path(config)
        assert resolved == tmp_path / expected
        assert resolved.parent == configured.parent


def test_suffixless_path_still_derives(tmp_path: Path):
    config = dataclasses.replace(create_default_config(), known_words_db_path=tmp_path / "known_words", language="zh")
    assert resolve_known_words_db_path(config) == tmp_path / "known_words.zh"

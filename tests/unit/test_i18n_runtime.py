"""Translator install logic: en no-ops, unknown is graceful, dir resolves."""

from PyQt6.QtWidgets import QApplication

from anki_miner.gui import i18n


def test_translations_dir_contains_en_catalog():
    d = i18n.translations_dir()
    assert (d / "anki_miner_en.ts").exists()


def test_available_languages_has_english():
    assert i18n.available_languages()["en"] == "English"


def test_install_translators_en_is_noop(qapp: QApplication):
    assert i18n.install_translators(qapp, "en") == []


def test_install_translators_unknown_is_graceful(qapp: QApplication):
    # An unknown code must not raise and must install no app translator.
    result = i18n.install_translators(qapp, "zz")
    assert isinstance(result, list)
    assert all("anki_miner_zz" not in t.filePath() for t in result)

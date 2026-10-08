"""Translator install logic: unknown is graceful, dir resolves."""

import pytest
from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDoubleSpinBox

from anki_miner.gui import i18n


def test_translations_dir_contains_en_catalog():
    d = i18n.translations_dir()
    assert (d / "anki_miner_en.ts").exists()


def test_available_languages_has_english():
    assert i18n.available_languages()["en"] == "English"


def test_install_translators_unknown_is_graceful(qapp: QApplication):
    # An unknown code must not raise and must install no app translator.
    result = i18n.install_translators(qapp, "zz")
    assert isinstance(result, list)
    assert all("anki_miner_zz" not in t.filePath() for t in result)


@pytest.fixture
def comma_system_locale():
    """Stand in for a host whose numeric locale writes a decimal comma (LC_NUMERIC=es_ES, de_DE).

    Without a default, QLocale() carries the system's separators, so a German default
    gives every widget what such a host gives it. Restored afterwards so the rest of
    the session keeps its locale.
    """
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    yield
    QLocale.setDefault(previous)


def _spin_box(qtbot) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    qtbot.addWidget(spin)
    spin.setRange(0.0, 10.0)
    spin.setDecimals(2)
    return spin


def test_spin_box_shows_a_period_decimal_on_a_comma_locale_host(qtbot, comma_system_locale):
    i18n.install_number_locale()
    spin = _spin_box(qtbot)
    spin.setValue(0.3)

    assert spin.text() == "0.30"


def test_spin_box_accepts_a_typed_period_decimal(qtbot, comma_system_locale):
    i18n.install_number_locale()
    spin = _spin_box(qtbot)
    edit = spin.lineEdit()
    edit.selectAll()
    QTest.keyClicks(edit, "0.5")
    QTest.keyClick(edit, Qt.Key.Key_Return)

    assert spin.value() == 0.5


def test_number_locale_keeps_the_thousands_comma(comma_system_locale):
    # Not QLocale.c(): its OmitGroupSeparator would print "184200".
    i18n.install_number_locale()

    assert QLocale().toString(184_200) == "184,200"

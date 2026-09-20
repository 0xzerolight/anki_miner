"""Persian and Arabic share a script but never a known-words set (S15).

``tests/e2e/test_ar_known_words_contamination.py`` pins the half that belongs to
Arabic: an Arabic gate cannot keep a Persian DECK out of an Arabic scan, so a
user excludes the deck. This file pins the other half, which is structural
rather than a setting — the two languages are partitioned by the DATABASE FILE
they get, not by any query or script test, so a word learnt in one is invisible
to the other whatever the user does.

That partition is load-bearing precisely because the script gate cannot help:
the four Persian-only letters are all inside the Arabic Unicode ranges.
"""

from __future__ import annotations

from dataclasses import replace

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.languages.fa.script import PersianScript
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.known_word_db import KnownWordDB

#: pe, che, zhe, gaf: the letters Persian adds to the Arabic alphabet.
PERSIAN_ONLY = ("\N{ARABIC LETTER PEH}", "\N{ARABIC LETTER TCHEH}", "\N{ARABIC LETTER JEH}", "\N{ARABIC LETTER GAF}")
#: ketab, "book" - spelled identically in Persian and in Arabic.
KETAB = "\N{ARABIC LETTER KEHEH}\N{ARABIC LETTER TEH}\N{ARABIC LETTER ALEF}\N{ARABIC LETTER BEH}"


def _db(home, language: str) -> KnownWordDB:
    config = replace(switch_language(AnkiMinerConfig(), language), known_words_db_path=home / "known_words.db")
    db = KnownWordDB(resolve_known_words_db_path(config), language=language)
    db.initialize()
    return db


def test_the_persian_only_letters_are_inside_the_arabic_ranges():
    """Why the script gate cannot separate the two: pe/che/zhe/gaf are Arabic-block."""
    for letter in PERSIAN_ONLY:
        assert 0x0600 <= ord(letter) <= 0x06FF
        assert PersianScript().contains_target_script(letter)
    # And the reverse: an Arabic-only string passes the Persian gate.
    assert PersianScript().contains_target_script(KETAB)


def test_persian_and_arabic_get_different_database_files(tmp_path):
    fa = replace(switch_language(AnkiMinerConfig(), "fa"), known_words_db_path=tmp_path / "known_words.db")
    ar = replace(switch_language(AnkiMinerConfig(), "ar"), known_words_db_path=tmp_path / "known_words.db")
    assert resolve_known_words_db_path(fa).name == "known_words.fa.db"
    assert resolve_known_words_db_path(ar).name == "known_words.ar.db"
    # Japanese alone keeps the bare name (no migration, same bytes).
    ja = replace(AnkiMinerConfig(), known_words_db_path=tmp_path / "known_words.db")
    assert resolve_known_words_db_path(ja).name == "known_words.db"


def test_a_word_known_in_arabic_is_not_known_in_persian(tmp_path):
    """Same spelling, same script, two databases: the partition is by construction."""
    arabic = _db(tmp_path, "ar")
    persian = _db(tmp_path, "fa")
    arabic.add_words({KETAB}, source="anki")
    assert KETAB in arabic.get_known_words()
    assert persian.get_known_words() == set()


def test_a_word_known_in_persian_is_not_known_in_arabic(tmp_path):
    persian = _db(tmp_path, "fa")
    arabic = _db(tmp_path, "ar")
    persian.add_words({KETAB}, source="user")
    assert KETAB in persian.get_known_words()
    assert arabic.get_known_words() == set()


def test_the_persian_database_folds_on_write(tmp_path):
    """The fa DB stores the profile's fold, so a ZWNJ spelling is one entry."""
    from anki_miner.languages.fa.script import ZWNJ, fa_fold

    persian = _db(tmp_path, "fa")
    mi = "\N{ARABIC LETTER MEEM}\N{ARABIC LETTER FARSI YEH}"
    ravam = "\N{ARABIC LETTER REH}\N{ARABIC LETTER WAW}\N{ARABIC LETTER MEEM}"
    persian.add_words({mi + ZWNJ + ravam, mi + ravam}, source="anki")
    known = persian.get_known_words()
    assert len(known) == 1
    assert fa_fold(mi + ZWNJ + ravam) in {fa_fold(word) for word in known}


def test_the_profile_is_the_only_thing_that_differs(tmp_path):
    """Both languages run the same code path; only the profile and the file change."""
    assert get_profile("fa").code == "fa"
    assert get_profile("ar").code == "ar"
    assert get_profile("fa").dedup_fold is not get_profile("ar").dedup_fold

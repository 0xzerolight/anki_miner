"""Ukrainian known-word ingestion through the real AnkiService scan (S15 contamination, the uk fold).

A Cyrillic gate cannot tell Ukrainian from Russian or Bulgarian: their decks land in the Ukrainian
known-words set unless excluded. The fake AnkiConnect ignores ``-deck:`` negations, so the exclusion
itself is pinned on the query. The ``network`` marker is for the real loopback socket the fake
server listens on -- nothing here is downloaded.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.anki_service import AnkiService

pytestmark = pytest.mark.network  # real loopback socket; suppresses the tripwire

ACUTE = "\N{COMBINING ACUTE ACCENT}"
RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"


def _config(fake_anki, home, language: str = "uk", **overrides) -> AnkiMinerConfig:
    base = switch_language(AnkiMinerConfig(), language)
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def _fronts(config: AnkiMinerConfig, text: str) -> set[str]:
    parser = get_profile(config.language).create_parser(config)
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=text, index=0, location_label="t")], False)
    return {word.mined_form for word in words}


def test_a_cyrillic_gate_ingests_other_cyrillic_decks_and_folds_ukrainian_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Українська", f"кни{ACUTE}жка", f"М{RSQUO}яч", "читати")
    _seed(service, "Русский", "книга", "читать")
    _seed(service, "Български", "ъгъл")
    _seed(service, "English", "the dog")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    assert {"книжка", "м'яч", "читати"} <= vocab  # stress, apostrophe and case folded (UK_DEDUP_FOLD)
    assert {"книга", "читать", "ъгъл"} <= vocab  # S15: the Cyrillic gate cannot tell uk from ru or bg
    assert "the dog" not in vocab and "日本語" not in vocab
    assert resolve_known_words_db_path(config).name == "known_words.uk.db"


def test_the_russian_and_ukrainian_known_word_files_are_separate(fake_anki, isolated_home):
    """The ru/uk pair: two Cyrillic languages, one Anki collection, separated three ways.

    The file name is the cheap half and is pinned elsewhere too. The half that would actually bite
    is engine-level -- a pymorphy3 built for the wrong language would fold a Ukrainian deck onto
    Russian lemmas -- so this asserts the contrast on a word the two languages spell identically.
    """
    uk = _config(fake_anki, isolated_home)
    ru = _config(fake_anki, isolated_home, language="ru")
    assert {resolve_known_words_db_path(uk).name, resolve_known_words_db_path(ru).name} == {
        "known_words.uk.db",
        "known_words.ru.db",
    }
    # `читала` is spelled identically in both languages and lemmatises differently in each.
    uk_fronts = _fronts(uk, "Студентка читала книжку.")
    ru_fronts = _fronts(ru, "Студентка читала книгу.")
    assert "читати" in uk_fronts and "читать" not in uk_fronts
    assert "читать" in ru_fronts and "читати" not in ru_fronts
    assert get_profile("uk").audio.cache_stem_prefix != get_profile("ru").audio.cache_stem_prefix


def test_the_two_folds_disagree_where_the_languages_do():
    """uk folds every apostrophe and has no yo rule; ru folds yo and leaves the apostrophe alone."""
    uk_fold, ru_fold = get_profile("uk").dedup_fold, get_profile("ru").dedup_fold
    assert uk_fold is not None and ru_fold is not None
    assert uk_fold(f"М{RSQUO}яч") == "м'яч" and ru_fold(f"М{RSQUO}яч") != "м'яч"
    assert ru_fold("Ёлка") == "елка" and uk_fold("Ёлка") == "ёлка"
    # Both fold stress and case, which is the half they genuinely share.
    assert uk_fold(f"Кни{ACUTE}жка") == "книжка" and ru_fold(f"Кни{ACUTE}га") == "книга"


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("Русский", "Български")))
    query = service._build_vocab_query()
    assert '-deck:"Русский"' in query and '-deck:"Български"' in query

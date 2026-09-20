"""The R36 form-lookup read path: storage.term_rows, the provider probe, the chain walk.

Three additive functions, each mirroring its existence-probe sibling. The one behaviour that is
easy to get wrong and impossible to see in production is the chain walk: a term with no rows must
be ABSENT from the result, because ``DefinitionService`` subtracts the answered terms from its
remaining list. Mapping a miss to ``[]`` would empty that list after the first provider and starve
every later one -- a silent all-miss whenever the language's dictionary is not first in the chain.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path

import pytest

from anki_miner.languages.he.script import HebrewDictKeys
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary import storage
from anki_miner.services.dictionary.importers.yomitan_importer import render_glossary_entry
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.storage import DictRow

FIXTURES = Path(__file__).resolve().parents[1].parent / "fixtures" / "he"
WTY = json.loads((FIXTURES / "wty_rows.json").read_text(encoding="utf-8"))
TOKENS = {
    row["id"]: row for row in (json.loads(line) for line in (FIXTURES / "tokens.jsonl").read_text("utf-8").splitlines())
}
FOLD_SITES = [json.loads(line) for line in (FIXTURES / "fold_sites.jsonl").read_text(encoding="utf-8").splitlines()]


def _surface(line_id: str, index: int) -> str:
    return TOKENS[line_id]["tokens"][index][0]


HE_KEYS = HebrewDictKeys()
#: A term that is ONLY some entry's reading: term_rows must not see it as a headword.
READING_ONLY = "reading-only-probe"


def _dict_rows():
    """The committed wty-he-en subset as storage rows, written with the Hebrew key folding.

    Built here rather than through ``import_yomitan_zip`` because this module must not depend on
    the language registry: the read path is what is under test, and the full importer route is
    exercised end to end by ``tests/unit/languages/test_he_dictionary.py``.
    """
    for term, _reading, definition_tags, rules, score, glossary, sequence, term_tags in WTY["term_rows"]:
        tags = [t for t in str(definition_tags).split(" ") if t] + [t for t in str(term_tags).split(" ") if t]
        content = render_glossary_entry(
            glossary if isinstance(glossary, list) else [glossary],
            definition_tags=[t for t in str(definition_tags).split(" ") if t],
            dict_id="wty-he-en",
            media_collector=None,
        )
        yield DictRow(
            term=term,
            reading="",
            content=content,
            tags=" ".join(tags),
            rules=str(rules or ""),
            score=int(score or 0),
            sequence=int(sequence) if sequence is not None else None,
        )
    yield DictRow(
        term="probe-headword",
        reading=READING_ONLY,
        content="<li>reading-bearing row</li>",
        tags="n",
        score=0,
        sequence=None,
    )


@pytest.fixture(scope="module")
def provider(tmp_path_factory) -> IndexedDictProvider:
    """A real index built from the committed wty-he-en subset, with the Hebrew key folding."""
    db_path = tmp_path_factory.mktemp("wty_he") / "index.sqlite"
    storage.create_index(db_path)
    storage.write_meta(db_path, {"schema_version": str(storage.SCHEMA_VERSION), "source_name": "wty-he-en"})
    assert storage.bulk_insert(db_path, _dict_rows(), keys=HE_KEYS) > 500
    found = IndexedDictProvider("wty-he-en", db_path, keys=HE_KEYS)
    assert found.load()
    return found


class _FakeProvider:
    """A minimal offline provider: answers only the terms it was given."""

    is_online = False

    def __init__(self, name: str, rows: dict[str, list[tuple[str, str]]], *, raises: bool = False) -> None:
        self.name = name
        self._rows = rows
        self._raises = raises
        self.seen: list[list[str]] = []

    def is_available(self) -> bool:
        return True

    def term_rows(self, terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        self.seen.append(list(terms))
        if self._raises:
            raise sqlite3.DatabaseError("boom")
        return {term: self._rows[term] for term in terms if term in self._rows}


class _EmptyAnsweringProvider(_FakeProvider):
    """The shape that breaks a naive walk: it answers EVERY term, with nothing."""

    def term_rows(self, terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        self.seen.append(list(terms))
        return {term: [] for term in terms}


class _OnlineProvider(_FakeProvider):
    is_online = True


def _service(*providers) -> DefinitionService:
    service = DefinitionService.__new__(DefinitionService)
    service._providers = list(providers)
    service.ensure_loaded = lambda: None  # type: ignore[method-assign]
    return service


# --------------------------------------------------------------------------
# storage.term_rows
# --------------------------------------------------------------------------


def test_an_inflected_key_returns_its_form_rows(provider):
    rows = provider.term_rows([_surface("he08", 2)])  # katavti
    [(_content, tags)] = [(c, t) for c, t in rows[_surface("he08", 2)]][:1]
    assert len(rows[_surface("he08", 2)]) == 2
    assert all("non-lemma" in t.split(" ") for _c, t in rows[_surface("he08", 2)])
    assert tags


def test_a_headword_returns_at_least_one_lemma_row(provider):
    word = _surface("he01", 2)  # sefer
    rows = provider.term_rows([word])
    assert any("non-lemma" not in tags.split(" ") for _content, tags in rows[word])


def test_an_absent_key_is_not_a_key_of_the_result(provider):
    """The whole point of the contract: a miss is absence, never an empty list."""
    missing = _surface("he05", 4)  # prof', absent at every spelling
    rows = provider.term_rows([missing])
    assert missing not in rows
    assert rows == {}


def test_a_pointed_query_and_its_bare_spelling_meet_the_same_rows(provider):
    [row] = [row for row in FOLD_SITES if row["note"] == "niqqud folds out"]
    rows = provider.term_rows([row["raw"], row["folded"]])
    assert rows[row["raw"]] == rows[row["folded"]]
    assert rows[row["raw"]]


def test_a_reading_only_match_contributes_nothing(provider):
    """term_rows is term-exact, the same rule as terms_exist: a reading is not a headword."""
    assert provider.term_rows([READING_ONLY]) == {}
    assert provider.term_rows(["probe-headword"])["probe-headword"]


def test_a_corrupt_index_degrades_to_an_empty_map(tmp_path):
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"not a database")
    found = IndexedDictProvider("broken", broken, keys=HE_KEYS)
    found.load()
    assert found.term_rows([_surface("he01", 2)]) == {}


def test_the_rows_come_back_in_storage_order(provider):
    """Best entry first: score DESC, then sequence, then id -- the order lookup itself uses."""
    word = _surface("he01", 2)
    conn = sqlite3.connect(provider._db_path)
    try:
        expected = [
            (content, tags or "")
            for content, tags in conn.execute(
                "SELECT content, tags FROM entries WHERE term = ? ORDER BY score DESC, sequence, id",
                (HE_KEYS.fold_term(word),),
            )
        ]
    finally:
        conn.close()
    assert provider.term_rows([word])[word] == expected


def test_the_storage_function_takes_the_key_folding_like_its_siblings():
    assert "keys" in inspect.signature(storage.term_rows).parameters


# --------------------------------------------------------------------------
# The chain walk -- the arrangement that fails today
# --------------------------------------------------------------------------


def test_a_non_hebrew_provider_first_does_not_starve_the_hebrew_one(provider):
    """The real shape: a JMdict-shaped dictionary ahead of the Hebrew one in the chain."""
    word = _surface("he08", 2)
    other = _FakeProvider("other-language", {})
    service = _service(other, provider)

    found = service.offline_term_rows([word])

    assert found[word] == provider.term_rows([word])[word]
    assert other.seen == [[word]]


def test_a_provider_that_answers_every_term_with_nothing_does_not_answer_them(provider):
    """An empty list is not an answer, whichever level produced it."""
    word = _surface("he08", 2)
    greedy = _EmptyAnsweringProvider("greedy", {})
    service = _service(greedy, provider)

    assert service.offline_term_rows([word])[word]


def test_the_first_provider_that_answers_a_term_wins(provider):
    word = _surface("he01", 2)
    first = _FakeProvider("first", {word: [("<p>mine</p>", "n masc")]})
    second = _FakeProvider("second", {word: [("<p>theirs</p>", "n fem")]})
    service = _service(first, second)

    assert service.offline_term_rows([word]) == {word: [("<p>mine</p>", "n masc")]}
    assert second.seen == []


def test_an_online_provider_and_one_without_the_method_are_skipped(provider):
    word = _surface("he01", 2)

    class _NoMethod:
        is_online = False
        name = "no-method"

        def is_available(self) -> bool:
            return True

    online = _OnlineProvider("online", {word: [("<p>net</p>", "n")]})
    service = _service(online, _NoMethod(), provider)

    assert service.offline_term_rows([word]) == {word: provider.term_rows([word])[word]}
    assert online.seen == []


def test_a_provider_that_raises_degrades_to_a_miss_and_the_walk_continues(provider):
    word = _surface("he01", 2)
    angry = _FakeProvider("angry", {}, raises=True)
    service = _service(angry, provider)

    assert service.offline_term_rows([word])[word]


def test_duplicate_requests_collapse_and_an_empty_request_is_empty(provider):
    word = _surface("he01", 2)
    service = _service(provider)
    assert service.offline_term_rows([]) == {}
    assert list(service.offline_term_rows([word, word])) == [word]


# --------------------------------------------------------------------------
# Purely additive
# --------------------------------------------------------------------------


def test_the_sibling_probes_keep_their_signatures():
    assert str(inspect.signature(storage.terms_exist)) == (
        "(conn: 'sqlite3.Connection', terms: 'list[str]', *, keys: 'DictKeyFolding | None' = None) -> 'set[str]'"
    )
    assert str(inspect.signature(IndexedDictProvider.has_terms)) == "(self, terms: 'list[str]') -> 'set[str]'"
    assert (
        str(inspect.signature(DefinitionService.offline_term_readings))
        == "(self, terms: 'list[str]') -> 'dict[str, list[str]]'"
    )

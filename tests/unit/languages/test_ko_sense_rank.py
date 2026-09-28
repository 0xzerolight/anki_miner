"""KRDICT rows lead with the basic sense (KO-01).

KRDICT gives every row score 0 and sequence 1, so without a row rank the import
order decides, and that is the homograph number: 먹다 1 "be deaf" opens the card
before 먹다 2 "eat", the -하다 suffix before the verb 하다, and 팔's stem
pointer (팔- → 팔다) before "arm". The rows below are KRDICT's own shapes (the
structured content its term banks carry), imported through the real importer
and read back through the provider that renders the card's Definition.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.yomitan_renderer import render_glossary_entry
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip

KEYS = get_profile("ko").dict_keys
_GLOSSES = re.compile(r'<span class="gloss-sc-span" lang="en">([^<]+)</span>')


def _entry(term: str, headword: str, tags: str | None, body: dict[str, Any], homograph: int | None) -> list[Any]:
    """One KRDICT term-bank row: bold headword (+ homograph number), then the body."""
    head: list[dict[str, Any]] = [{"tag": "span", "style": {"fontWeight": "bold"}, "content": headword, "lang": "ko"}]
    if homograph is not None:
        head.append(
            {"tag": "span", "style": {"fontSize": "0.7em", "verticalAlign": "super"}, "content": str(homograph)}
        )
    glossary = [{"type": "structured-content", "content": [{"tag": "span", "content": head, "lang": "ko"}, body]}]
    return [term, "", tags, "", 0, glossary, 1, ""]


def _row(term: str, headword: str, tags: str | None, gloss: str, homograph: int | None = None) -> list[Any]:
    body = {
        "tag": "div",
        "content": [{"tag": "div", "content": [{"tag": "span", "content": gloss, "lang": "en"}], "lang": "en"}],
        "lang": "en",
    }
    return _entry(term, headword, tags, body, homograph)


def _pointer(term: str, headword: str, text: str) -> list[Any]:
    """A row with no English gloss that only names the entry it points at."""
    return _entry(term, headword, None, {"tag": "div", "content": text}, None)


#: In KRDICT's own index order for each term.
ROWS = [
    _row("먹다", "먹다", "Verb", "be deaf", 1),
    _row("먹다", "먹다", "Verb ⭐⭐⭐", "eat; have; consume; take", 2),
    _row("먹다", "먹다", "Auxiliary Verb", "meokda", 3),
    _row("하다", "-하다", "Affix ⭐⭐⭐", "-hada"),
    _row("하다", "하다", "Verb ⭐⭐⭐", "do; perform", 1),
    _pointer("팔", "팔-", "(팔고, 팔아, 팔아서, 팔면, 팔았다, 팔아라)→ 팔다"),
    _row("팔", "팔", "Noun ⭐⭐⭐", "arm", 1),
    _row("의사", "의사", "Noun ⭐⭐", "mind; intention", 1),
    _row("의사", "의사", "Noun ⭐⭐⭐", "doctor; physician", 3),
    _row("가톨릭", "가톨릭", "Noun", "Catholicism"),
]


def _content(row: list[Any]) -> str:
    return render_glossary_entry(row[5], definition_tags=str(row[2] or "").split())


def _tags(row: list[Any]) -> str:
    return str(row[2] or "")


class TestSenseRank:
    def test_a_three_star_sense_ranks_first(self) -> None:
        eat = ROWS[1]
        assert KEYS.sense_rank(_content(eat), _tags(eat), None) == 0

    def test_a_sense_below_three_stars_ranks_after_it(self) -> None:
        deaf, mind = ROWS[0], ROWS[7]
        assert KEYS.sense_rank(_content(deaf), _tags(deaf), None) == 1
        assert KEYS.sense_rank(_content(mind), _tags(mind), None) == 1

    def test_an_affix_row_ranks_after_every_sense_even_at_three_stars(self) -> None:
        affix = ROWS[3]
        assert KEYS.sense_rank(_content(affix), _tags(affix), None) == 2

    def test_a_pointer_row_ranks_last(self) -> None:
        pointer = ROWS[5]
        assert KEYS.sense_rank(_content(pointer), _tags(pointer), None) == 3

    def test_the_token_part_of_speech_changes_nothing(self) -> None:
        eat = ROWS[1]
        assert KEYS.sense_rank(_content(eat), _tags(eat), "VV") == 0


@pytest.fixture
def krdict(tmp_path: Path):
    zip_path = build_yomitan_zip(
        tmp_path / "src" / "krdict.zip",
        title="KRDICT EN",
        term_banks=[ROWS],
        tag_banks=[],
        index_extra={"sequenced": False},
    )
    result = import_yomitan_zip(zip_path, tmp_path / "dicts", language="ko")
    provider = IndexedDictProvider(
        result.dict_id, tmp_path / "dicts" / result.dict_id / "index.sqlite", display_name="KRDICT EN", keys=KEYS
    )
    provider.load()
    yield provider
    provider.close()


@pytest.mark.parametrize(
    ("word", "first"),
    [
        ("먹다", "eat; have; consume; take"),
        ("하다", "do; perform"),
        ("팔", "arm"),
        ("의사", "doctor; physician"),
        # One row and no stars: nothing to reorder.
        ("가톨릭", "Catholicism"),
    ],
)
def test_the_card_opens_on_the_basic_sense(krdict, word: str, first: str) -> None:
    rendered = krdict.lookup_many([(word, None)])[word]
    assert rendered is not None
    first_row = rendered.split('<li class="gloss-item"')[1]
    assert _GLOSSES.findall(first_row) == [first]

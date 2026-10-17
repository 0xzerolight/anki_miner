"""DictKeyFolding parameterisation of the dictionary import/query key seam.

The ``keys=None`` branch must reproduce today's Japanese behaviour byte-for-byte
(same index bytes, same schema version, same homograph mask), and a non-None
folding must actually reach storage — an accepted-and-ignored keyword is this
seam's known failure mode, so the forwarding proof uses a stub folding whose
answers differ from ja's.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary import storage
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    read_meta,
    write_meta,
)


@pytest.fixture
def ja_keys():
    return get_profile("ja").dict_keys


class _UpperKeys:
    """Stub folding: uppercases both key spaces, keeps every row.

    Deliberately unlike ja on ASCII input, so a helper that accepts ``keys`` and
    ignores it cannot pass the forwarding assertions below.
    """

    def __init__(self) -> None:
        self.term_calls: list[str] = []
        self.reading_calls: list[str | None] = []
        self.mask_calls: list[str] = []

    def fold_term(self, s: str) -> str:
        self.term_calls.append(s)
        return s.upper()

    def fold_reading(self, s: str | None) -> str | None:
        self.reading_calls.append(s)
        return None if s is None else s.upper()

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        self.mask_calls.append(word)
        return [True] * len(rows)


_ROWS = [
    DictRow(term="日本語", reading="ニホンゴ", content="<div>Japanese</div>", sequence=1),
    DictRow(term="にほんご", reading="にほんご", content="<div>Japanese</div>", sequence=1),
    DictRow(term="レイド", reading="レイド", content="<div>raid</div>", sequence=2),
    DictRow(term="零度", reading="れいど", content="<div>zero degrees</div>", sequence=3),
]


def _build(db: Path, rows=_ROWS, **kwargs) -> Path:
    create_index(db)
    bulk_insert(db, rows, **kwargs)
    write_meta(
        db,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": "Test Dict",
            "format": "yomitan",
            "entry_count": str(len(rows)),
        },
    )
    return db


# (a) the ja folding is today's storage helpers, verbatim.


def test_ja_fold_reading_is_storage_fold_reading(ja_keys):
    for value in ("コーヒー", "にほんご", "レイド", "Ni3Hao3", ""):
        assert ja_keys.fold_reading(value) == storage._fold_reading(value)
    assert ja_keys.fold_reading(None) is None
    assert storage._fold_reading(None) is None


def test_ja_fold_term_is_nfc(ja_keys):
    for value in ("日本語", "ｶﾞ", "が", "ガ", "Ni3Hao3"):
        assert ja_keys.fold_term(value) == unicodedata.normalize("NFC", value)


# (b) the ja mask is storage._homograph_keep_mask for Rule A / A' / B.


@pytest.mark.parametrize(
    ("word", "rows", "lemma"),
    [
        # Rule A: a term-exact row exists.
        ("レイド", [("レイド", "raid"), ("零度", "zero degrees")], None),
        # Rule A, content carve-out: reading-only row carries the SAME gloss.
        ("にほんご", [("にほんご", "Japanese"), ("日本語", "Japanese")], None),
        # Rule A': no term-exact row, lemma names the right lexeme.
        ("ゆう", [("有", "to have"), ("言う", "to say"), ("夕", "evening")], "言う"),
        # Rule B: kana-only query, no term-exact or lemma-exact row.
        ("しゃべる", [("喋る", "to chat"), ("シャベル", "shovel")], None),
        # Kanji query, no term-exact row: mask untouched.
        ("零度", [("れいど", "something else")], None),
        # Empty row set.
        ("レイド", [], None),
    ],
)
def test_ja_homograph_keep_mask_matches_storage(ja_keys, word, rows, lemma):
    assert ja_keys.homograph_keep_mask(word, list(rows), lemma) == storage._homograph_keep_mask(word, list(rows), lemma)


# (c)/(d) keys=None and the ja folding write byte-equal, schema-v6 indexes.


def test_ja_keys_import_is_byte_equal_to_none(tmp_path: Path, ja_keys):
    none_db = _build(tmp_path / "none" / "index.sqlite")
    ja_db = _build(tmp_path / "ja" / "index.sqlite", keys=ja_keys)

    assert hashlib.sha256(none_db.read_bytes()).hexdigest() == hashlib.sha256(ja_db.read_bytes()).hexdigest()


def test_schema_version_stays_six_for_both(tmp_path: Path, ja_keys):
    none_db = _build(tmp_path / "none" / "index.sqlite")
    ja_db = _build(tmp_path / "ja" / "index.sqlite", keys=ja_keys)

    assert read_meta(none_db)["schema_version"] == "6"
    assert read_meta(ja_db)["schema_version"] == "6"


def test_ja_keys_query_matches_none_branch(tmp_path: Path, ja_keys):
    db = _build(tmp_path / "d" / "index.sqlite")
    conn = storage.open_readonly(db)
    try:
        for word in ("日本語", "にほんご", "レイド", "ニホンゴ"):
            assert storage.lookup(conn, word, keys=ja_keys) == storage.lookup(conn, word)
            assert storage.lookup_with_rules(conn, word, keys=ja_keys) == storage.lookup_with_rules(conn, word)
        pairs = [("日本語", "にほんご"), ("レイド", None)]
        assert storage.lookup_many(conn, pairs, keys=ja_keys) == storage.lookup_many(conn, pairs)
        terms = ["日本語", "零度", "missing"]
        assert storage.terms_exist(conn, terms, keys=ja_keys) == storage.terms_exist(conn, terms)
        seq_pairs = [("日本語", "ニホンゴ"), ("レイド", "レイド")]
        assert storage.exact_term_sequences(conn, seq_pairs, keys=ja_keys) == storage.exact_term_sequences(
            conn, seq_pairs
        )
    finally:
        conn.close()


# (e) the provider forwards its folding into storage rather than ignoring it.


def test_provider_forwards_keys_to_storage(tmp_path: Path):
    keys = _UpperKeys()
    rows = [DictRow(term="abc", reading="dee", content="<div>abc</div>", sequence=1)]
    db = _build(tmp_path / "upper" / "index.sqlite", rows=rows, keys=keys)

    # bulk_insert folded the stored term through the stub.
    assert storage.read_meta(db)["schema_version"] == "6"
    conn = storage.open_readonly(db)
    try:
        assert {t for (t,) in conn.execute("SELECT term FROM entries")} == {"ABC"}
        assert {r for (r,) in conn.execute("SELECT reading FROM entries")} == {"DEE"}
    finally:
        conn.close()

    keys.term_calls.clear()
    keys.reading_calls.clear()
    keys.mask_calls.clear()

    provider = IndexedDictProvider("upper-dict", db, display_name="Upper", keys=keys)
    assert provider.load() is True
    assert provider._keys is keys

    assert provider.lookup("abc") is not None
    assert "abc" in keys.term_calls
    # The scope sees the FOLDED word, exactly as the ja branch passes the
    # NFC-normalised one.
    assert "ABC" in keys.mask_calls

    assert provider.lookup_many([("abc", None)])["abc"] is not None
    assert provider.lookup_fallback("abc", 0) is not None
    assert provider.has_terms(["abc"]) == {"abc"}
    assert provider.exact_term_sequences([("abc", "dee")]) == {("ABC", "DEE"): {1}}

    # A provider with no folding cannot see the same rows — proof the keyword is
    # load-bearing and not accepted-and-ignored.
    plain = IndexedDictProvider("upper-dict", db, display_name="Upper")
    assert plain.load() is True
    assert plain.lookup("abc") is None
    assert plain.has_terms(["abc"]) == set()


# (f) the chain builder supplies the active profile's folding, by identity.


def test_build_provider_chain_supplies_profile_folding(tmp_path: Path):
    _build(tmp_path / "jmdict-english" / "index.sqlite")
    config = replace(
        AnkiMinerConfig(),
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),),
        language="ja",
    )
    registry = DictionaryRegistry(tmp_path)
    registry.load()
    chain = registry.build_provider_chain(config)

    assert len(chain) == 1
    assert isinstance(chain[0], IndexedDictProvider)
    assert chain[0]._keys is get_profile("ja").dict_keys

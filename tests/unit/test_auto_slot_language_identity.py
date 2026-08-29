"""One source, two languages, two slots — without forking a legacy ja slot.

``resolve_auto_store_id`` reuses a slot whose recorded identity matches the one
being imported. Before this, the identity was the source's name and revision
alone, so the same CC-CEDICT-shaped file imported once for Japanese and once for
Chinese landed on ONE slot: the second import silently relabelled the first, and
the language-filtered chain build then dropped it from one of the two sessions.

The language now takes part in that comparison, under ``meta_language``
semantics on BOTH sides — an absent key reads as ``"ja"``. That is what keeps
every slot imported before the transition (no ``language`` meta row at all)
matching a Japanese re-import exactly as it does today, instead of forking a
second copy of a dictionary the user already has.

The Japanese *identity dict* is unchanged, key for key: it carries no
``language`` entry at all (``language_kwarg``'s omit-when-ja shape). That is
load-bearing rather than cosmetic — the fork id is a SHA-256 over the identity
dict, so one extra key would move every already-forked Japanese slot to a new
id and orphan the installed one. The literals below are captured from the
pre-change code path.

Frequency CSVs are the vehicle: the smallest input that reaches
``resolve_auto_store_id``, and the identity dict it builds
(``{"source_name", "source_revision"}``) is the same shape three of the four
families use.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anki_miner.services._sqlite_index import meta_language
from anki_miner.services.frequency import source_importer, storage
from anki_miner.services.frequency.source_importer import import_frequency_source

#: Captured by running the ladder on the pre-change code (probe in the task
#: report): the base id for "neko.csv", and the fork a second JA identity on the
#: same base takes. Both must survive the language becoming part of the compare.
JA_BASE_ID = "neko"
JA_FORK_ID = "neko-e8f6422d24fc"


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    path = tmp_path / "src" / "neko.csv"
    path.parent.mkdir(parents=True)
    path.write_text("term,rank\n猫,5\n", encoding="utf-8")
    return path


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    return tmp_path / "freqs"


def _language_of(dest: Path, source_id: str) -> str:
    return meta_language(storage.read_meta_cached(dest / source_id / "index.sqlite"))


def _rank_of(dest: Path, source_id: str) -> object:
    """Prove the slot is a live index, not just a directory that exists."""
    with sqlite3.connect(dest / source_id / "index.sqlite") as conn:
        return conn.execute("SELECT rank FROM entries WHERE term = ?", ("猫",)).fetchone()[0]


def _strip_language_stamp(dest: Path, source_id: str) -> None:
    """Age a slot back to its pre-transition shape: no ``language`` meta row.

    Every slot on an existing user's disk looks like this. The sidecar goes too,
    so nothing can answer the language question from a cache the real legacy
    slot never had.
    """
    slot = dest / source_id
    (slot / "meta.json").unlink(missing_ok=True)
    with sqlite3.connect(slot / "index.sqlite") as conn:
        conn.execute("DELETE FROM meta WHERE key = 'language'")
        conn.commit()


def test_a_legacy_unstamped_slot_is_reused_by_a_ja_reimport(csv_path: Path, dest: Path) -> None:
    """The hard invariant: no existing Japanese user grows a duplicate slot."""
    first = import_frequency_source(csv_path, dest)
    _strip_language_stamp(dest, first.source_id)

    again = import_frequency_source(csv_path, dest, overwrite=True)

    assert again.source_id == first.source_id == JA_BASE_ID
    assert sorted(p.name for p in dest.iterdir()) == [JA_BASE_ID]


def test_a_second_language_forks_its_own_slot(csv_path: Path, dest: Path) -> None:
    """The same file imported for Chinese never lands on the Japanese slot."""
    ja = import_frequency_source(csv_path, dest)
    zh = import_frequency_source(csv_path, dest, language="zh")

    assert zh.source_id != ja.source_id
    assert _language_of(dest, ja.source_id) == "ja"
    assert _language_of(dest, zh.source_id) == "zh"
    assert _rank_of(dest, ja.source_id) == _rank_of(dest, zh.source_id)


def test_the_forked_slot_is_reused_by_a_reimport_of_its_own_language(csv_path: Path, dest: Path) -> None:
    """A zh re-import finds the zh slot, so the fork is stable, not unbounded."""
    import_frequency_source(csv_path, dest)
    zh = import_frequency_source(csv_path, dest, language="zh")

    zh_again = import_frequency_source(csv_path, dest, language="zh", overwrite=True)

    assert zh_again.source_id == zh.source_id
    assert len(list(dest.iterdir())) == 2


def test_a_ja_import_never_relabels_a_zh_slot(csv_path: Path, dest: Path) -> None:
    """The other direction of the collision: zh installed first, ja second.

    An identity dict with no language key still compares as ``"ja"``, so the
    occupied Chinese slot is a mismatch and Japanese forks instead of
    overwriting it.
    """
    zh = import_frequency_source(csv_path, dest, language="zh")
    ja = import_frequency_source(csv_path, dest)

    assert ja.source_id != zh.source_id
    assert _language_of(dest, zh.source_id) == "zh"
    assert _language_of(dest, ja.source_id) == "ja"


def test_ja_passes_no_language_key_while_zh_does(csv_path: Path, dest: Path, monkeypatch) -> None:
    """The digest input stays byte-identical for Japanese.

    The fork id hashes the identity dict, so this is the assertion that stops a
    future edit from "tidying" the omission into an unconditional
    ``"language": language``.
    """
    seen: list[dict[str, str]] = []
    real = source_importer.resolve_auto_store_id

    def _spy(root, base_id, family, identity):
        seen.append(dict(identity))
        return real(root, base_id, family, identity)

    monkeypatch.setattr(source_importer, "resolve_auto_store_id", _spy)

    import_frequency_source(csv_path, dest)
    import_frequency_source(csv_path, dest, language="zh")

    assert "language" not in seen[0]
    assert seen[0] == {"source_name": "neko", "source_revision": ""}
    assert seen[1]["language"] == "zh"


def test_ja_slot_ids_match_the_pre_change_ladder(csv_path: Path, dest: Path) -> None:
    """Base id and fork id, both literal, both captured before the change."""
    base = import_frequency_source(csv_path, dest)
    # Same derived base id, different identity: the JA fork branch.
    fork = import_frequency_source(csv_path, dest, source_name="Another List")

    assert base.source_id == JA_BASE_ID
    assert fork.source_id == JA_FORK_ID

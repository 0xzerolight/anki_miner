"""Seed a minimal offline dictionary covering the E2E subtitle's vocabulary.

The harness must mine with NO network (no Jisho fallback), so every lemma the
test subtitle yields needs a local definition. This module writes a tiny
Yomitan-style indexed dictionary at ``<dicts_root>/<dict_id>/index.sqlite`` with
one entry per lemma in
:data:`tests.e2e.fixtures_subtitle.EXPECTED_LEMMAS` — each carrying the lemma's
kana reading (from :data:`tests.e2e.fixtures_subtitle.LEMMA_READINGS`) and a
simple English gloss wrapped in the ``gloss-item`` ``<li>`` shape the indexed
provider expects.

It reuses the real storage primitives
(:func:`anki_miner.services.dictionary.storage.create_index` /
``bulk_insert`` / ``write_meta``) and the same meta-key shape the integration
suite (``tests/integration/test_dictionary_chain.py``) writes, so
:class:`anki_miner.services.dictionary.registry.DictionaryRegistry` discovers the
dict with a valid schema.
"""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)
from tests.e2e.fixtures_subtitle import EXPECTED_LEMMAS, LEMMA_READINGS

__all__ = ["DEFAULT_DICT_ID", "GLOSSES", "seed_offline_dict"]

#: Default dictionary id (the on-disk folder name under ``dicts_root``).
DEFAULT_DICT_ID = "e2e-dict"

#: Lemma -> simple English gloss. One per ``EXPECTED_LEMMAS`` entry; the exact
#: wording is irrelevant to the harness (it only needs a non-empty definition to
#: confirm the offline chain hit), but real-ish glosses keep failures readable.
GLOSSES: dict[str, str] = {
    "新しい": "new",
    "本": "book",
    "買う": "to buy",
    "今日": "today",
    "学校": "school",
    "勉強": "study",
    "美味しい": "delicious; tasty",
    "料理": "cooking; dish",
    "食べる": "to eat",
    "友達": "friend",
    "公園": "park",
    "走る": "to run",
}


def _rows() -> list[DictRow]:
    """Build one :class:`DictRow` per expected lemma (reading + gloss)."""
    rows: list[DictRow] = []
    for sequence, lemma in enumerate(EXPECTED_LEMMAS, start=1):
        gloss = GLOSSES.get(lemma, lemma)
        rows.append(
            DictRow(
                term=lemma,
                reading=LEMMA_READINGS.get(lemma),
                content=f'<li class="gloss-item">{gloss}</li>',
                sequence=sequence,
            )
        )
    return rows


def seed_offline_dict(dicts_root: Path, dict_id: str = DEFAULT_DICT_ID) -> Path:
    """Create a minimal indexed dictionary covering every expected lemma.

    Writes ``<dicts_root>/<dict_id>/index.sqlite`` with one entry per lemma in
    :data:`EXPECTED_LEMMAS` plus the standard Yomitan meta rows, mirroring the
    integration suite's seeding helper.

    Args:
        dicts_root: The dicts root the registry will scan (typically a tmp dir,
            or the harness's isolated ``dicts/`` folder).
        dict_id: Folder name / dictionary id to create. Defaults to
            ``"e2e-dict"``.

    Returns:
        Path to the created ``index.sqlite``.
    """
    folder = dicts_root / dict_id
    folder.mkdir(parents=True, exist_ok=True)
    db_path = folder / "index.sqlite"

    rows = _rows()
    create_index(db_path)
    bulk_insert(db_path, rows)
    write_meta(
        db_path,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": dict_id,
            "format": "yomitan",
            "entry_count": str(len(rows)),
        },
    )
    return db_path

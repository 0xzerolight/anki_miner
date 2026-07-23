"""Tests for managed SQLite slot containment and ownership proofs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anki_miner.services._sqlite_index import (
    is_generated_store_artifact,
    prove_owned_slot,
    resolve_managed_slot,
    scan_index_root,
    validate_index_schema,
    validate_store_id,
    write_ownership_marker,
)
from anki_miner.services.audio_packs import storage as audio_storage
from anki_miner.services.dictionary import storage as dictionary_storage
from anki_miner.services.frequency import storage as frequency_storage


@pytest.mark.parametrize(
    "store_id",
    ("", ".", "..", "/absolute", "nested/slot", r"nested\slot"),
)
def test_validate_store_id_rejects_non_component_ids(store_id: str) -> None:
    with pytest.raises(ValueError):
        validate_store_id(store_id)


def test_resolve_managed_slot_returns_direct_child_of_resolved_root(tmp_path: Path) -> None:
    root = tmp_path / "root"

    assert resolve_managed_slot(root, "safe-slot") == root.resolve() / "safe-slot"


@pytest.mark.parametrize("store_id", ("slot.bak-new", "slot.tomb-new"))
def test_resolve_managed_slot_reserves_generated_syntax_for_new_ids(
    tmp_path: Path,
    store_id: str,
) -> None:
    with pytest.raises(ValueError):
        resolve_managed_slot(tmp_path, store_id)


def test_resolve_managed_slot_allows_existing_legacy_generated_syntax_id(
    tmp_path: Path,
) -> None:
    slot = tmp_path / "slot.bak-existing"
    slot.mkdir()

    assert resolve_managed_slot(tmp_path, slot.name) == slot


def _create_index(
    slot: Path,
    *,
    entries_sql: str,
    meta: dict[str, str],
    tags_sql: str | None = None,
) -> Path:
    slot.mkdir(parents=True)
    db_path = slot / "index.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(entries_sql)
        if tags_sql is not None:
            conn.execute(tags_sql)
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", meta.items())
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_dictionary_schema_requires_entries_and_tags_columns(tmp_path: Path) -> None:
    valid = tmp_path / "valid" / "index.sqlite"
    dictionary_storage.create_index(valid)
    dictionary_storage.write_meta(
        valid,
        {
            "schema_version": str(dictionary_storage.SCHEMA_VERSION),
            "source_name": "Valid",
        },
    )
    missing_rules = _create_index(
        tmp_path / "missing-rules",
        entries_sql=("CREATE TABLE entries (content TEXT, tags TEXT, sequence INTEGER)"),
        tags_sql=("CREATE TABLE tags (name TEXT, category TEXT, ord INTEGER, notes TEXT, score REAL)"),
        meta={
            "schema_version": str(dictionary_storage.SCHEMA_VERSION),
            "source_name": "Plausible",
        },
    )
    missing_tag_score = _create_index(
        tmp_path / "missing-tag-score",
        entries_sql=("CREATE TABLE entries (content TEXT, tags TEXT, rules TEXT, sequence INTEGER)"),
        tags_sql="CREATE TABLE tags (name TEXT, category TEXT, ord INTEGER, notes TEXT)",
        meta={
            "schema_version": str(dictionary_storage.SCHEMA_VERSION),
            "source_name": "Plausible",
        },
    )
    missing_term = _create_index(
        tmp_path / "missing-term",
        entries_sql=("CREATE TABLE entries (content TEXT, tags TEXT, rules TEXT, sequence INTEGER)"),
        tags_sql=("CREATE TABLE tags (name TEXT, category TEXT, ord INTEGER, notes TEXT, score REAL)"),
        meta={
            "schema_version": str(dictionary_storage.SCHEMA_VERSION),
            "source_name": "Plausible",
        },
    )

    assert validate_index_schema(valid, "dictionary")
    assert not validate_index_schema(missing_rules, "dictionary")
    assert not validate_index_schema(missing_tag_score, "dictionary")
    assert not validate_index_schema(missing_term, "dictionary")


def test_frequency_schema_uses_version_specific_columns(tmp_path: Path) -> None:
    v1 = _create_index(
        tmp_path / "v1",
        entries_sql="CREATE TABLE entries (term TEXT, reading TEXT, rank INTEGER)",
        meta={"schema_version": "1"},
    )
    v1_missing_reading = _create_index(
        tmp_path / "v1-missing-reading",
        entries_sql="CREATE TABLE entries (term TEXT, rank INTEGER)",
        meta={"schema_version": "1"},
    )
    v2 = _create_index(
        tmp_path / "v2",
        entries_sql=("CREATE TABLE entries (term TEXT, reading TEXT, rank INTEGER, display_value TEXT)"),
        meta={"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )
    v2_missing_display = _create_index(
        tmp_path / "v2-missing-display",
        entries_sql="CREATE TABLE entries (term TEXT, reading TEXT, rank INTEGER)",
        meta={"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )
    v2_missing_term = _create_index(
        tmp_path / "v2-missing-term",
        entries_sql=("CREATE TABLE entries (reading TEXT, rank INTEGER, display_value TEXT)"),
        meta={"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )

    assert validate_index_schema(v1, "frequency")
    assert not validate_index_schema(v1_missing_reading, "frequency")
    assert validate_index_schema(v2, "frequency")
    assert not validate_index_schema(v2_missing_display, "frequency")
    assert not validate_index_schema(v2_missing_term, "frequency")


def test_audio_schema_requires_lookup_columns(tmp_path: Path) -> None:
    valid = tmp_path / "valid" / "index.sqlite"
    audio_storage.create_index(valid)
    audio_storage.write_meta(
        valid,
        {
            "schema_version": str(audio_storage.SCHEMA_VERSION),
            "pack_id": "valid",
        },
    )
    missing_speaker = _create_index(
        tmp_path / "missing-speaker",
        entries_sql="CREATE TABLE entries (file TEXT, source TEXT)",
        meta={
            "schema_version": str(audio_storage.SCHEMA_VERSION),
            "pack_id": "missing-speaker",
        },
    )
    missing_expression = _create_index(
        tmp_path / "missing-expression",
        entries_sql="CREATE TABLE entries (file TEXT, source TEXT, speaker TEXT)",
        meta={
            "schema_version": str(audio_storage.SCHEMA_VERSION),
            "pack_id": "missing-expression",
        },
    )

    assert validate_index_schema(valid, "audio")
    assert not validate_index_schema(missing_speaker, "audio")
    assert not validate_index_schema(missing_expression, "audio")


def test_schema_validation_never_creates_missing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "missing" / "index.sqlite"

    assert not validate_index_schema(db_path, "dictionary")
    assert not db_path.exists()


@pytest.mark.parametrize("family", ("dictionary", "frequency", "audio"))
def test_exact_marker_proves_owned_slot_with_unexpected_child(
    tmp_path: Path,
    family: str,
) -> None:
    slot = tmp_path / "slot"
    slot.mkdir()
    (slot / "unexpected.bin").write_bytes(b"unrelated generated payload")
    write_ownership_marker(slot, "slot", family)

    assert prove_owned_slot(tmp_path, "slot", family)


def test_legacy_physical_stores_prove_family_ownership(tmp_path: Path) -> None:
    dict_db = tmp_path / "dict" / "index.sqlite"
    dictionary_storage.create_index(dict_db)
    dictionary_storage.write_meta(
        dict_db,
        {
            "schema_version": "3",
            "source_name": "Legacy dictionary",
        },
    )
    freq_db = tmp_path / "freq" / "index.sqlite"
    frequency_storage.create_index(freq_db)
    frequency_storage.write_meta(
        freq_db,
        {"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )
    audio_db = tmp_path / "audio" / "index.sqlite"
    audio_storage.create_index(audio_db)
    audio_storage.write_meta(
        audio_db,
        {
            "schema_version": str(audio_storage.SCHEMA_VERSION),
            "pack_id": "audio",
        },
    )

    assert prove_owned_slot(tmp_path, "dict", "dictionary")
    assert not validate_index_schema(dict_db, "dictionary")
    assert prove_owned_slot(tmp_path, "freq", "frequency")
    assert prove_owned_slot(tmp_path, "audio", "audio")


def test_foreign_plausible_meta_does_not_prove_ownership(tmp_path: Path) -> None:
    _create_index(
        tmp_path / "foreign",
        entries_sql="CREATE TABLE entries (payload TEXT)",
        meta={
            "schema_version": str(dictionary_storage.SCHEMA_VERSION),
            "source_name": "Looks plausible",
        },
    )

    assert not prove_owned_slot(tmp_path, "foreign", "dictionary")


def test_audio_physical_proof_requires_exact_pack_id(tmp_path: Path) -> None:
    db_path = tmp_path / "expected" / "index.sqlite"
    audio_storage.create_index(db_path)
    audio_storage.write_meta(
        db_path,
        {
            "schema_version": str(audio_storage.SCHEMA_VERSION),
            "pack_id": "different",
        },
    )

    assert not prove_owned_slot(tmp_path, "expected", "audio")


def test_present_contradictory_marker_is_authoritative_negative(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "expected" / "index.sqlite"
    dictionary_storage.create_index(db_path)
    dictionary_storage.write_meta(
        db_path,
        {
            "schema_version": str(dictionary_storage.SCHEMA_VERSION),
            "source_name": "Expected",
        },
    )
    write_ownership_marker(db_path.parent, "other", "dictionary")

    assert not prove_owned_slot(tmp_path, "expected", "dictionary")


def test_symlinked_index_never_proves_container_ownership(tmp_path: Path) -> None:
    external_db = tmp_path / "external" / "index.sqlite"
    dictionary_storage.create_index(external_db)
    dictionary_storage.write_meta(
        external_db,
        {
            "schema_version": str(dictionary_storage.SCHEMA_VERSION),
            "source_name": "External",
        },
    )
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "unrelated.txt").write_text("keep", encoding="utf-8")
    (foreign / "index.sqlite").symlink_to(external_db)

    assert not prove_owned_slot(tmp_path, "foreign", "dictionary")


def test_symlink_slot_never_proves_ownership(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    write_ownership_marker(outside, "slot", "dictionary")
    root = tmp_path / "root"
    root.mkdir()
    (root / "slot").symlink_to(outside, target_is_directory=True)

    assert not prove_owned_slot(root, "slot", "dictionary")


@pytest.mark.parametrize(
    "name",
    (
        "slot.bak-123",
        "slot.tomb-123",
        "slot.corrupt-123",
        ".staging-slot-123",
        ".hidden-staging",
    ),
)
def test_generated_store_artifact_classifier_accepts_generated_names(name: str) -> None:
    assert is_generated_store_artifact(name)


@pytest.mark.parametrize("name", ("slot", "bak-123", "slot.backup-123"))
def test_generated_store_artifact_classifier_rejects_canonical_names(name: str) -> None:
    assert not is_generated_store_artifact(name)


def test_scan_index_root_prefilters_every_generated_artifact(tmp_path: Path) -> None:
    parsed: list[str] = []
    for name in (
        "slot.bak-123",
        "slot.tomb-123",
        "slot.corrupt-123",
        ".staging-slot-123",
    ):
        db_path = tmp_path / name / "index.sqlite"
        dictionary_storage.create_index(db_path)
        dictionary_storage.write_meta(
            db_path,
            {
                "schema_version": str(dictionary_storage.SCHEMA_VERSION),
                "source_name": name,
            },
        )

    result = scan_index_root(
        tmp_path,
        lambda child, _db, _meta: parsed.append(child.name) or child.name,
    )

    assert result == {}
    assert parsed == []

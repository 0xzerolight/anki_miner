"""Tests for managed SQLite slot containment and ownership proofs."""

from __future__ import annotations

import sqlite3
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services import _sqlite_index
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
from anki_miner.services.audio_packs.importer import import_audio_pack
from anki_miner.services.dictionary import storage as dictionary_storage
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.frequency import storage as frequency_storage
from anki_miner.services.frequency.source_importer import import_frequency_source
from anki_miner.services.pitch_accent.source_importer import import_pitch_source
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip


@pytest.mark.parametrize(
    "store_id",
    ("", ".", "..", "/absolute", "nested/slot", r"nested\slot"),
)
def test_validate_store_id_rejects_non_component_ids(store_id: str) -> None:
    with pytest.raises(ValueError):
        validate_store_id(store_id)


@pytest.mark.parametrize(
    "store_id",
    (
        "CON",
        "prn",
        "Aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
        "COM¹",
        "com²",
        "Com³",
        "LPT¹",
        "lpt²",
        "Lpt³",
        "con.backup",
    ),
)
def test_validate_store_id_rejects_windows_device_basenames(store_id: str) -> None:
    with pytest.raises(ValueError):
        validate_store_id(store_id)


def test_resolve_managed_slot_returns_direct_child_of_resolved_root(tmp_path: Path) -> None:
    root = tmp_path / "root"

    assert resolve_managed_slot(root, "safe-slot") == root.resolve() / "safe-slot"


def test_auto_ids_disambiguate_slug_collisions_and_reimport_stably(tmp_path: Path) -> None:
    first = tmp_path / "A B.csv"
    second = tmp_path / "A-B.csv"
    first.write_text("term,rank\n猫,1\n", encoding="utf-8")
    second.write_text("term,rank\n犬,2\n", encoding="utf-8")
    root = tmp_path / "freqs"

    first_result = import_frequency_source(first, root)
    second_result = import_frequency_source(second, root)

    assert first_result.source_id == "a-b"
    assert second_result.source_id.startswith("a-b-")
    assert second_result.source_id != first_result.source_id
    repeated = import_frequency_source(second, root, overwrite=True)
    assert repeated.source_id == second_result.source_id


@pytest.mark.parametrize("family", ("dictionary", "frequency", "pitch", "audio"))
def test_auto_id_importers_do_not_route_around_foreign_slot(tmp_path: Path, family: str) -> None:
    root = tmp_path / "managed" / family
    foreign = root / "slot"
    foreign.mkdir(parents=True)
    payload = foreign / "keep.txt"
    payload.write_text("foreign", encoding="utf-8")

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    if family == "dictionary":
        source = build_yomitan_zip(inputs / "source.zip", title="slot", revision="")
        import_source = partial(import_yomitan_zip, source, root, overwrite=True)
    elif family == "frequency":
        source = inputs / "slot.csv"
        source.write_text("term,rank\n猫,1\n", encoding="utf-8")
        import_source = partial(import_frequency_source, source, root, overwrite=True)
    elif family == "pitch":
        source = inputs / "slot.csv"
        source.write_text("ねこ,猫,1\n", encoding="utf-8")
        import_source = partial(import_pitch_source, source, root, overwrite=True)
    else:
        source = inputs / "slot"
        source.mkdir()
        (source / "ねこ - 猫.mp3").touch()
        import_source = partial(import_audio_pack, source, root, overwrite=True)

    with pytest.raises(SetupError, match="not an Anki Miner-managed"):
        import_source()

    assert payload.read_text(encoding="utf-8") == "foreign"
    assert [child.name for child in root.iterdir()] == ["slot"]


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
    current = _create_index(
        tmp_path / "current",
        entries_sql=("CREATE TABLE entries (term TEXT, reading TEXT, rank INTEGER, display_value TEXT)"),
        meta={"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )
    current_missing_display = _create_index(
        tmp_path / "current-missing-display",
        entries_sql="CREATE TABLE entries (term TEXT, reading TEXT, rank INTEGER)",
        meta={"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )
    current_missing_term = _create_index(
        tmp_path / "current-missing-term",
        entries_sql=("CREATE TABLE entries (reading TEXT, rank INTEGER, display_value TEXT)"),
        meta={"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )

    assert not validate_index_schema(v1, "frequency")
    assert not validate_index_schema(v1_missing_reading, "frequency")
    assert validate_index_schema(current, "frequency")
    assert not validate_index_schema(current_missing_display, "frequency")
    assert not validate_index_schema(current_missing_term, "frequency")
    assert prove_owned_slot(tmp_path, "v1", "frequency")


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


def test_read_meta_missing_database_does_not_create_file(tmp_path: Path) -> None:
    db_path = tmp_path / "missing" / "index.sqlite"

    assert _sqlite_index.read_meta(db_path) == {}
    assert not db_path.exists()


def test_read_meta_does_not_recreate_file_deleted_after_precheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "index.sqlite"
    real_exists = Path.exists
    first_check = True

    def exists_once(path: Path) -> bool:
        nonlocal first_check
        if path == db_path and first_check:
            first_check = False
            return True
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", exists_once)

    with pytest.raises(sqlite3.OperationalError):
        _sqlite_index.read_meta(db_path)

    assert not db_path.exists()


def test_read_meta_does_not_modify_corrupt_file(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    payload = b"not a sqlite database"
    db_path.write_bytes(payload)

    with pytest.raises(sqlite3.DatabaseError):
        _sqlite_index.read_meta(db_path)

    assert db_path.read_bytes() == payload


def test_open_readonly_closes_connection_when_pragma_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = MagicMock()
    conn.execute.side_effect = sqlite3.OperationalError("PRAGMA failed")
    monkeypatch.setattr(_sqlite_index.sqlite3, "connect", MagicMock(return_value=conn))

    with pytest.raises(sqlite3.OperationalError, match="PRAGMA failed"):
        _sqlite_index.open_readonly(tmp_path / "index.sqlite")

    conn.close.assert_called_once_with()


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


@pytest.mark.parametrize("version", range(3, dictionary_storage.SCHEMA_VERSION + 1))
def test_every_schema_version_we_ever_wrote_proves_ownership(tmp_path: Path, version: int) -> None:
    """Ownership must span the whole written range, not {oldest, current}.

    Ownership answers "did we write this directory"; staleness is a separate
    question (``DictMeta.schema_ok``). A version-pair check silently un-owns the
    immediately-previous schema on every bump — precisely the dictionaries an
    upgrade must repair — so Reimport All would report a user's entire installed
    set as missing-source and make them re-add each one by hand.
    """
    slot = tmp_path / f"v{version}"
    db_path = slot / "index.sqlite"
    slot.mkdir(parents=True)
    dictionary_storage.create_index(db_path)
    dictionary_storage.write_meta(
        db_path,
        {
            "schema_version": str(version),
            "format": "yomitan",
            "source_name": f"Written at v{version}",
            "entry_count": "0",
        },
    )

    assert prove_owned_slot(tmp_path, f"v{version}", "dictionary")


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


class TestReadonlySqliteUri:
    """Windows extended-length prefixes must not leak into the file: URI."""

    def test_unc_extended_prefix_stripped(self):
        from anki_miner.services._sqlite_index import _strip_extended_length_prefix

        assert _strip_extended_length_prefix("\\\\?\\UNC\\server\\share\\db") == "\\\\server\\share\\db"

    def test_drive_extended_prefix_stripped(self):
        from anki_miner.services._sqlite_index import _strip_extended_length_prefix

        assert _strip_extended_length_prefix("\\\\?\\C:\\dicts\\a\\index.sqlite") == "C:\\dicts\\a\\index.sqlite"

    def test_plain_paths_untouched(self):
        from anki_miner.services._sqlite_index import _strip_extended_length_prefix

        assert _strip_extended_length_prefix("/home/u/.anki_miner/dicts/a/index.sqlite") is None
        assert _strip_extended_length_prefix("C:\\dicts\\a\\index.sqlite") is None

    def test_readonly_uri_roundtrip_opens(self, tmp_path):
        import sqlite3

        from anki_miner.services._sqlite_index import readonly_sqlite_uri

        db = tmp_path / "weird #? %dir" / "index.sqlite"
        db.parent.mkdir()
        sqlite3.connect(db).close()
        conn = sqlite3.connect(readonly_sqlite_uri(db), uri=True)
        conn.close()

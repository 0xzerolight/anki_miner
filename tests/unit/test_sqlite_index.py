"""Tests for the shared SQLite-index plumbing (Task 15 / SM7, SM9, SL2).

Covers the two hardening fixes layered onto ``_sqlite_index.py``:
* ``write_meta`` opens its SQLite connection with an explicit busy timeout
  instead of the bare default, mirroring ``known_word_db._connect``.
* the ``meta.json`` sidecar is published via ``atomic_write_path`` (never a
  raw ``write_text``) and ``read_meta_cached``'s freshness compare uses
  nanosecond-resolution mtimes so a same-second sidecar/DB write pair is
  ordered correctly.
"""

from __future__ import annotations

import json
import sqlite3
import stat as stat_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.services import _sqlite_index


def _create_meta_table(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
    finally:
        conn.close()


class TestWriteMetaTimeout:
    def test_opens_with_explicit_busy_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """write_meta must not rely on the bare sqlite3.connect default; it
        passes an explicit timeout, mirroring known_word_db._connect."""
        db_path = tmp_path / "index.sqlite"
        _create_meta_table(db_path)

        captured: dict[str, object] = {}
        real_connect = sqlite3.connect

        def fake_connect(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return real_connect(*args)

        monkeypatch.setattr(_sqlite_index.sqlite3, "connect", fake_connect)

        _sqlite_index.write_meta(db_path, {"schema_version": "1"})

        assert captured["args"][0] == db_path
        assert captured["kwargs"].get("timeout") == 5.0

    def test_round_trips_through_sidecar(self, tmp_path: Path):
        """Sanity: write_meta's normal (unmocked) path still upserts and publishes."""
        db_path = tmp_path / "index.sqlite"
        _create_meta_table(db_path)

        _sqlite_index.write_meta(db_path, {"schema_version": "1"})
        _sqlite_index.write_meta(db_path, {"schema_version": "2", "source_name": "jmdict"})

        assert _sqlite_index.read_meta(db_path) == {"schema_version": "2", "source_name": "jmdict"}
        sidecar = tmp_path / "meta.json"
        # The published payload is the meta rows plus the reserved column
        # record; what a meta reader sees back is the rows alone.
        published = json.loads(sidecar.read_text(encoding="utf-8"))
        assert published.pop(_sqlite_index._SIDECAR_COLUMNS_KEY) == '{"entries": [], "tags": []}'
        assert published == {"schema_version": "2", "source_name": "jmdict"}


class TestWriteMetaSidecarAtomic:
    def test_uses_atomic_write_path_not_a_bare_write_text(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """The sidecar publish must go through atomic_write_path (temp file +
        os.replace), never a direct Path.write_text on the destination."""
        db_path = tmp_path / "index.sqlite"
        db_path.write_bytes(b"")

        real_atomic_write_path = _sqlite_index.atomic_write_path
        calls: list[Path] = []

        def spy(dest: Path):
            calls.append(dest)
            return real_atomic_write_path(dest)

        monkeypatch.setattr(_sqlite_index, "atomic_write_path", spy)

        destination_write_text_calls: list[Path] = []
        real_write_text = Path.write_text

        def tracking_write_text(self, *args, **kwargs):
            if self == tmp_path / "meta.json":
                destination_write_text_calls.append(self)
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", tracking_write_text)

        _sqlite_index.write_meta_sidecar(db_path, {"schema_version": "1"})

        assert calls == [tmp_path / "meta.json"]
        # write_text is called on the *temp* sibling atomic_write_path hands
        # back, never directly on the final "meta.json" destination path.
        assert destination_write_text_calls == []
        assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"schema_version": "1"}

    def test_mid_write_failure_leaves_old_sidecar_intact(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """A crash/exception while staging the new sidecar must not corrupt or
        truncate the previous one -- atomic_write_path only replaces on success."""
        db_path = tmp_path / "index.sqlite"
        db_path.write_bytes(b"")
        sidecar = tmp_path / "meta.json"
        sidecar.write_text(json.dumps({"schema_version": "1"}), encoding="utf-8")

        def boom(self, *args, **kwargs):
            raise OSError("disk full mid-write")

        monkeypatch.setattr(Path, "write_text", boom)

        # write_meta_sidecar is best-effort: it must swallow the failure, not raise.
        _sqlite_index.write_meta_sidecar(db_path, {"schema_version": "2"})

        assert json.loads(sidecar.read_text(encoding="utf-8")) == {"schema_version": "1"}
        # No stray temp file left behind under the failed staging attempt.
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".anki-miner-")]
        assert leftovers == []


class TestReadMetaCachedNanosecondFreshness:
    def test_uses_st_mtime_ns_not_float_st_mtime(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """A sidecar written a fraction of a second before the DB, in the same
        wall-clock second, must be treated as stale. Two crafted stat results
        share an identical float st_mtime (the precision float truncation would
        collapse them to) while their st_mtime_ns values correctly order the DB
        as newer -- only the nanosecond compare tells them apart."""
        db_path = tmp_path / "index.sqlite"
        db_path.write_bytes(b"")
        sidecar = tmp_path / "meta.json"
        sidecar.write_text(json.dumps({"schema_version": "1"}), encoding="utf-8")

        shared_float = 1_700_000_000.123456
        db_ns = 1_700_000_000_123_456_700
        sidecar_ns = 1_700_000_000_123_456_600  # 100ns OLDER than the db
        assert sidecar_ns < db_ns  # the fixture must actually exercise ordering

        real_stat = Path.stat

        def fake_stat(self, *args, **kwargs):
            if self == db_path:
                return SimpleNamespace(st_mtime=shared_float, st_mtime_ns=db_ns, st_mode=stat_module.S_IFREG | 0o644)
            if self == sidecar:
                return SimpleNamespace(
                    st_mtime=shared_float, st_mtime_ns=sidecar_ns, st_mode=stat_module.S_IFREG | 0o644
                )
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", fake_stat)

        fallback_calls: list[Path] = []

        def read_meta_fn(path: Path) -> dict[str, str]:
            fallback_calls.append(path)
            return {"schema_version": "2"}

        result = _sqlite_index.read_meta_cached(db_path, read_meta_fn)

        assert result == {"schema_version": "2"}
        assert fallback_calls == [db_path]

    def test_fresh_sidecar_still_short_circuits_the_sqlite_read(self, tmp_path: Path):
        """Unaffected control case: a genuinely newer sidecar is still served
        from the cache without touching read_meta_fn."""
        db_path = tmp_path / "index.sqlite"
        db_path.write_bytes(b"")
        sidecar = tmp_path / "meta.json"
        sidecar.write_text(json.dumps({"schema_version": "1"}), encoding="utf-8")

        import os
        import time

        now_ns = time.time_ns()
        os.utime(db_path, ns=(now_ns, now_ns))
        os.utime(sidecar, ns=(now_ns + 1_000_000, now_ns + 1_000_000))

        def read_meta_fn(path: Path) -> dict[str, str]:
            raise AssertionError("must not fall through when the sidecar is fresh")

        result = _sqlite_index.read_meta_cached(db_path, read_meta_fn)

        assert result == {"schema_version": "1"}


# --- Sidecar-answered schema validation ------------------------------------
#
# ``validate_index_schema`` costs one SQLite open plus a PRAGMA per store, and
# startup recovery pays it per configured slot before the first paint. The
# sidecar already caches the meta rows; recording the physical columns beside
# them lets it cache the whole verdict.

_AUDIO_ENTRIES = "expression TEXT, file TEXT, source TEXT, speaker TEXT"
_DICTIONARY_ENTRIES = "term TEXT, content TEXT, tags TEXT, rules TEXT, sequence INTEGER"
_DICTIONARY_TAGS = "name TEXT, category TEXT, ord INTEGER, notes TEXT, score REAL"
_FREQUENCY_ENTRIES = "term TEXT, reading TEXT, rank INTEGER, display_value TEXT"
_PITCH_ENTRIES = "reading TEXT, kanji TEXT, pattern TEXT, nasal TEXT, devoice TEXT"


def _current_schema_version(family: str) -> int:
    from anki_miner.services.audio_packs.storage import SCHEMA_VERSION as AUDIO
    from anki_miner.services.dictionary.storage import SCHEMA_VERSION as DICTIONARY
    from anki_miner.services.frequency.storage import SCHEMA_VERSION as FREQUENCY
    from anki_miner.services.pitch_accent.storage import SCHEMA_VERSION as PITCH

    return {"audio": AUDIO, "dictionary": DICTIONARY, "frequency": FREQUENCY, "pitch": PITCH}[family]


def _build_index(db_path: Path, *, entries: str, tags: str | None, meta: dict[str, str]) -> None:
    """Create a family-shaped index and publish its sidecar through write_meta."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"CREATE TABLE entries ({entries})")
        if tags is not None:
            conn.execute(f"CREATE TABLE tags ({tags})")
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
    finally:
        conn.close()
    _sqlite_index.write_meta(db_path, meta)


def _no_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError(f"the sidecar had to answer without opening SQLite: {args!r}")

    monkeypatch.setattr(_sqlite_index.sqlite3, "connect", boom)


class TestWriteMetaRecordsPhysicalColumns:
    def test_it_records_both_validated_tables_empty_list_included(self, tmp_path: Path):
        """``entries`` and ``tags`` are always keyed, even when absent from the
        database: only an always-present key lets a reader tell "this table does
        not exist" from "this sidecar predates column recording"."""
        db_path = tmp_path / "index.sqlite"
        _build_index(db_path, entries=_AUDIO_ENTRIES, tags=None, meta={"schema_version": "2"})

        payload = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))

        assert json.loads(payload[_sqlite_index._SIDECAR_COLUMNS_KEY]) == {
            "entries": ["expression", "file", "source", "speaker"],
            "tags": [],
        }

    def test_read_meta_cached_never_surfaces_the_recorded_columns(self, tmp_path: Path):
        """The sidecar caches meta ROWS; a caller that asked for meta must not
        see a key the meta table never held."""
        db_path = tmp_path / "index.sqlite"
        _build_index(
            db_path,
            entries=_AUDIO_ENTRIES,
            tags=None,
            meta={"schema_version": "2", "pack_id": "pack"},
        )

        def read_meta_fn(path: Path) -> dict[str, str]:
            raise AssertionError("must not fall through when the sidecar is fresh")

        assert _sqlite_index.read_meta_cached(db_path, read_meta_fn) == {
            "schema_version": "2",
            "pack_id": "pack",
        }
        assert _sqlite_index.read_meta(db_path) == {"schema_version": "2", "pack_id": "pack"}


class TestValidateIndexSchemaCached:
    @pytest.mark.parametrize(
        ("family", "entries", "tags", "version", "expected"),
        [
            ("audio", _AUDIO_ENTRIES, None, None, True),
            ("audio", "expression TEXT, file TEXT, source TEXT", None, None, False),
            ("audio", _AUDIO_ENTRIES, None, 999, False),
            ("dictionary", _DICTIONARY_ENTRIES, _DICTIONARY_TAGS, None, True),
            ("dictionary", _DICTIONARY_ENTRIES, None, None, False),
            ("dictionary", _DICTIONARY_ENTRIES, "name TEXT, category TEXT", None, False),
            ("frequency", _FREQUENCY_ENTRIES, None, None, True),
            ("frequency", "term TEXT, reading TEXT, rank INTEGER", None, None, False),
            ("pitch", _PITCH_ENTRIES, None, None, True),
            ("pitch", "reading TEXT, kanji TEXT, pattern TEXT", None, None, False),
        ],
    )
    def test_it_returns_the_verdict_the_sqlite_path_returns(
        self,
        tmp_path: Path,
        family: str,
        entries: str,
        tags: str | None,
        version: int | None,
        expected: bool,
    ):
        """The sidecar is a cache of the answer, never a second policy."""
        db_path = tmp_path / "index.sqlite"
        schema_version = _current_schema_version(family) if version is None else version
        _build_index(db_path, entries=entries, tags=tags, meta={"schema_version": str(schema_version)})

        assert _sqlite_index.validate_index_schema(db_path, family) is expected
        assert _sqlite_index.validate_index_schema_cached(db_path, family) is expected

    def test_a_recorded_sidecar_answers_without_opening_sqlite(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db_path = tmp_path / "index.sqlite"
        _build_index(db_path, entries=_AUDIO_ENTRIES, tags=None, meta={"schema_version": "2"})

        _no_sqlite(monkeypatch)

        assert _sqlite_index.validate_index_schema_cached(db_path, "audio") is True

    @pytest.mark.parametrize("bad_version", ["", "two", "  "])
    def test_an_unparseable_recorded_version_is_invalid_not_a_fall_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_version: str
    ):
        """The SQLite path treats a non-integer schema_version as invalid; a
        fresh sidecar holds that same row, so it must answer the same way."""
        db_path = tmp_path / "index.sqlite"
        _build_index(db_path, entries=_AUDIO_ENTRIES, tags=None, meta={"schema_version": bad_version})

        assert _sqlite_index.validate_index_schema(db_path, "audio") is False
        _no_sqlite(monkeypatch)
        assert _sqlite_index.validate_index_schema_cached(db_path, "audio") is False

    def test_a_sidecar_without_recorded_columns_falls_through(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Slots imported before column recording: the schema_version alone is
        not enough to answer, so the full check still runs."""
        db_path = tmp_path / "index.sqlite"
        _build_index(db_path, entries=_AUDIO_ENTRIES, tags=None, meta={"schema_version": "2"})
        sidecar = tmp_path / "meta.json"
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        del payload[_sqlite_index._SIDECAR_COLUMNS_KEY]
        sidecar.write_text(json.dumps(payload), encoding="utf-8")

        opened: list[str] = []
        real_connect = sqlite3.connect
        monkeypatch.setattr(
            _sqlite_index.sqlite3,
            "connect",
            lambda target, *a, **kw: (opened.append(str(target)), real_connect(target, *a, **kw))[1],
        )

        assert _sqlite_index.validate_index_schema_cached(db_path, "audio") is True
        assert opened, "the full check must still open the index"

    def test_a_sidecar_older_than_the_index_falls_through(self, tmp_path: Path):
        """A rewritten index and its unrefreshed sidecar: SQLite decides."""
        import os

        db_path = tmp_path / "index.sqlite"
        _build_index(db_path, entries=_AUDIO_ENTRIES, tags=None, meta={"schema_version": "999"})
        sidecar = tmp_path / "meta.json"
        older = db_path.stat().st_mtime_ns - 1_000_000
        os.utime(sidecar, ns=(older, older))

        assert _sqlite_index.validate_index_schema_cached(db_path, "audio") is False

        def read_meta_fn(path: Path) -> dict[str, str]:
            return {"fell": "through"}

        assert _sqlite_index.read_meta_cached(db_path, read_meta_fn) == {"fell": "through"}

    def test_a_symlinked_index_is_invalid_whatever_the_sidecar_says(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The regular-file check is nofollow and runs first, so a symlinked (or
        absent) index is refused before the sidecar is consulted at all — a
        live sidecar must not launder a link out of the slot."""
        real = tmp_path / "real.sqlite"
        _build_index(real, entries=_AUDIO_ENTRIES, tags=None, meta={"schema_version": "2"})
        (tmp_path / "meta.json").replace(tmp_path / "linked-meta.json")

        slot = tmp_path / "slot"
        slot.mkdir()
        (slot / "index.sqlite").symlink_to(real)
        (slot / "meta.json").write_text((tmp_path / "linked-meta.json").read_text(encoding="utf-8"), encoding="utf-8")

        _no_sqlite(monkeypatch)

        assert _sqlite_index.validate_index_schema_cached(slot / "index.sqlite", "audio") is False
        assert _sqlite_index.validate_index_schema_cached(tmp_path / "absent.sqlite", "audio") is False

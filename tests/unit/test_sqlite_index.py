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
        assert json.loads(sidecar.read_text(encoding="utf-8")) == {
            "schema_version": "2",
            "source_name": "jmdict",
        }


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

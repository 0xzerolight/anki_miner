"""Shared SQLite-index plumbing for the index-backed resource families.

Three resource families store their data as ``<root>/<id>/index.sqlite`` folders
with a small ``meta`` key/value table and a ``meta.json`` sidecar: dictionaries
(:mod:`anki_miner.services.dictionary.storage`), frequency sources
(:mod:`anki_miner.services.frequency.storage`), and audio packs
(:mod:`anki_miner.services.audio_packs.storage`). This module owns the
infrastructure they share so a fix (e.g. the URI-escaping guard in
:func:`open_readonly`) lands once instead of being hand-propagated ×3:

* the meta upsert + ``meta.json`` sidecar refresh (:func:`write_meta`),
* the raw meta read (:func:`read_meta`) and its sidecar-cached variant
  (:func:`read_meta_cached`),
* the read-only, thread-shareable connection opener (:func:`open_readonly`),
* the registry discovery scan loop (:func:`scan_index_root`).

Each storage module re-exports the meta/readonly helpers (importers and the
storage test suites depend on those paths) and keeps its own schema, row
dataclasses, and lookup queries. Each registry keeps its own ``Meta`` dataclass
and ``schema_ok`` policy inside the ``parse`` callable it hands to
:func:`scan_index_root`.

Connection idiom: these helpers use explicit ``try/finally conn.close()`` rather
than the sqlite3 ``with`` context manager, because ``with`` commits/rolls back
but does NOT close the connection — closing explicitly keeps the db file from
being held open across an importer's staging-dir cleanup (matters on Windows
where open file handles block directory deletion).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Sidecar filename living next to each ``index.sqlite``. Holds the resource's
# ``meta`` rows as JSON so a registry ``load()`` can skip the SQLite open on
# every app startup. Refreshed whenever ``write_meta`` runs.
_META_SIDECAR = "meta.json"


def write_meta(
    db_path: Path,
    items: dict[str, str],
    *,
    value_transform: Callable[[str], str | None] | None = None,
    sidecar_name: str = _META_SIDECAR,
) -> None:
    """Upsert ``meta`` rows and refresh the ``meta.json`` sidecar.

    ``value_transform`` (used by the dictionary layer to surrogate-scrub values,
    Issue #67) is applied to each value before it is bound; ``None`` stores the
    value verbatim. The sidecar lets the next :func:`read_meta_cached` call avoid
    re-opening SQLite when nothing changed.
    """
    conn = sqlite3.connect(db_path)
    try:
        for key, value in items.items():
            stored = value_transform(value) if value_transform is not None else value
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, stored),
            )
        conn.commit()
        full_meta = {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()
    write_meta_sidecar(db_path, full_meta, sidecar_name=sidecar_name)


def read_meta(db_path: Path) -> dict[str, str]:
    """Read all ``meta`` rows. Returns an empty dict if the file is missing."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()


def read_meta_cached(
    db_path: Path,
    read_meta_fn: Callable[[Path], dict[str, str]],
    *,
    sidecar_name: str = _META_SIDECAR,
) -> dict[str, str]:
    """Read ``meta`` rows via the ``meta.json`` sidecar when it is fresh.

    Falls through to ``read_meta_fn`` and rewrites the sidecar when:
    * the sidecar is missing,
    * ``index.sqlite`` is newer than the sidecar,
    * the sidecar is unreadable / not valid JSON.

    ``read_meta_fn`` is passed in (rather than calling :func:`read_meta`
    directly) so each storage module routes the fall-through through *its own*
    module-level ``read_meta`` — the seam the storage tests patch to assert the
    SQLite open is skipped on the hot startup path.
    """
    if not db_path.exists():
        return {}
    sidecar = db_path.parent / sidecar_name
    try:
        if sidecar.is_file() and sidecar.stat().st_mtime >= db_path.stat().st_mtime:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("meta sidecar miss for %s: %s", db_path, e)

    meta = read_meta_fn(db_path)
    write_meta_sidecar(db_path, meta, sidecar_name=sidecar_name)
    return meta


def write_meta_sidecar(db_path: Path, meta: dict[str, str], *, sidecar_name: str = _META_SIDECAR) -> None:
    """Best-effort sidecar write. Cache misses are logged, not raised — the
    next :func:`read_meta_cached` call simply falls back to the SQLite read."""
    sidecar = db_path.parent / sidecar_name
    try:
        sidecar.write_text(json.dumps(meta), encoding="utf-8")
    except OSError as e:  # pragma: no cover - defensive
        logger.debug("Failed to write meta sidecar %s: %s", sidecar, e)


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a read-only connection. Safe to share across threads.

    ``check_same_thread=False`` is required because providers/fetchers are
    constructed on the GUI thread (by service_factory) but consumed by worker
    threads. The connection is read-only (``PRAGMA query_only=ON``) so concurrent
    reads are safe under sqlite3's serialized access mode.
    """
    # Build the file: URI via Path.as_uri() so URI-significant characters in the
    # path (``#`` fragment, ``?`` query, ``%`` escape) are percent-encoded. A
    # raw f-string would let a root path containing any of these truncate the
    # path and point sqlite at the wrong (or nonexistent) file. as_uri() needs
    # an absolute path, so resolve first.
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only=ON")
    return conn


def scan_index_root(
    root: Path,
    parse: Callable[[Path, Path, dict[str, str]], _T | None],
    *,
    child_prefilter: Callable[[Path], bool] | None = None,
    exception_types: tuple[type[Exception], ...] = (sqlite3.DatabaseError,),
    warn_label: str = "index",
) -> dict[str, _T]:
    """Scan ``root`` for ``<child>/index.sqlite`` folders and build a meta map.

    Each direct subdirectory containing an ``index.sqlite`` is a candidate. For
    each candidate the meta is read via :func:`read_meta_cached` (sidecar-cached)
    and handed to ``parse(child, db_path, meta)``; a non-``None`` return is stored
    under ``child.name`` (a ``None`` return means "skip this child" — the audio
    layer uses it for its drop-schema-mismatch-at-scan policy).

    Parameters let each family keep its behavior:
    * ``child_prefilter`` runs *before* the ``index.sqlite`` check and the meta
      read — audio uses it to skip importer staging (hidden ``.`` dirs) and
      overwrite backups (``.bak-`` siblings). Return ``True`` to keep the child.
    * ``exception_types`` widens the meta-read guard — audio catches
      ``(sqlite3.Error, OSError)``; dictionary/frequency keep the narrower
      ``sqlite3.DatabaseError``.
    * ``warn_label`` names the resource in the scan/corruption warnings.

    An ``OSError`` while listing ``root`` (permission denied, stale NFS) yields an
    empty map with a warning rather than propagating.
    """
    result: dict[str, _T] = {}
    try:
        if not root.is_dir():
            return result
        children = sorted(root.iterdir())
    except OSError as e:
        logger.warning(
            "Could not scan %s folder '%s': %s — none will be loaded",
            warn_label,
            root,
            e,
        )
        return result
    for child in children:
        if not child.is_dir():
            continue
        if child_prefilter is not None and not child_prefilter(child):
            continue
        db = child / "index.sqlite"
        if not db.exists():
            continue
        try:
            meta = read_meta_cached(db, read_meta)
        except exception_types as e:
            logger.warning("Skipping corrupt %s %s: %s", warn_label, child.name, e)
            continue
        parsed = parse(child, db, meta)
        if parsed is not None:
            result[child.name] = parsed
    return result

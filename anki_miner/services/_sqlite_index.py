"""Shared SQLite-index plumbing for the index-backed resource families.

Four resource families store their data as ``<root>/<id>/index.sqlite`` folders
with a small ``meta`` key/value table and a ``meta.json`` sidecar: dictionaries
(:mod:`anki_miner.services.dictionary.storage`), frequency sources
(:mod:`anki_miner.services.frequency.storage`), audio packs
(:mod:`anki_miner.services.audio_packs.storage`), and pitch accent sources
(:mod:`anki_miner.services.pitch_accent.storage`). This module owns the
infrastructure they share so a fix (e.g. the URI-escaping guard in
:func:`open_readonly`) lands once instead of being hand-propagated ×4:

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

import hashlib
import json
import logging
import os
import sqlite3
import stat
from pathlib import Path, PureWindowsPath
from typing import Callable, Literal, TypedDict, TypeVar

from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.slug import is_windows_device_basename

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Sidecar filename living next to each ``index.sqlite``. Holds the resource's
# ``meta`` rows as JSON so a registry ``load()`` can skip the SQLite open on
# every app startup. Refreshed whenever ``write_meta`` runs.
_META_SIDECAR = "meta.json"
_OWNERSHIP_MARKER = ".anki-miner-owned.json"

# Reserved sidecar key holding the physical column list, so a reader can settle
# a schema question the meta rows alone cannot. Namespaced out of the meta key
# space and stripped by :func:`read_meta_cached`: the sidecar caches meta ROWS,
# and a caller that asked for meta must not be handed a key the meta table never
# held. Additive — a sidecar written before this existed simply lacks it, and
# every reader treats that as "cannot answer", not as "no columns".
_SIDECAR_COLUMNS_KEY = "__index_columns__"

# The tables :func:`validate_index_schema` queries, and the PRAGMA that reads
# each. Spelled out rather than interpolated so no table name is ever built into
# SQL at runtime. Both are recorded on every write, the empty list included: only
# an always-present key distinguishes "this table does not exist" from "this
# sidecar predates column recording".
_SIDECAR_COLUMN_PRAGMAS = {
    "entries": "PRAGMA table_info(entries)",
    "tags": "PRAGMA table_info(tags)",
}

StoreFamily = Literal["dictionary", "frequency", "audio", "pitch"]

_DICTIONARY_ENTRY_COLUMNS = frozenset(("term", "content", "tags", "rules", "sequence"))
_DICTIONARY_TAG_COLUMNS = frozenset(("name", "category", "ord", "notes", "score"))
_FREQUENCY_V1_COLUMNS = frozenset(("term", "reading", "rank"))
_FREQUENCY_V2_COLUMNS = _FREQUENCY_V1_COLUMNS | {"display_value"}
_AUDIO_ENTRY_COLUMNS = frozenset(("expression", "file", "source", "speaker"))
_PITCH_ENTRY_COLUMNS = frozenset(("reading", "kanji", "pattern", "nasal", "devoice"))


def validate_store_id(store_id: str) -> None:
    """Require one portable, non-traversing filesystem path component."""
    if (
        not isinstance(store_id, str)
        or not store_id
        or store_id in (".", "..")
        or "/" in store_id
        or "\\" in store_id
        or "\x00" in store_id
        or Path(store_id).is_absolute()
        or bool(PureWindowsPath(store_id).drive)
        or is_windows_device_basename(store_id)
    ):
        raise ValueError(f"Invalid managed store id: {store_id!r}")


def resolve_managed_slot(root: Path, store_id: str) -> Path:
    """Resolve *root* and return its direct, unresolved child *store_id*.

    Generated-artifact syntax is reserved for recovery files. Existing legacy
    slots with such names remain addressable, but no new slot may claim one.
    """
    validate_store_id(store_id)
    try:
        resolved_root = root.resolve()
        final = resolved_root / store_id
        if final.parent.resolve() != resolved_root:
            raise ValueError(f"Managed store escapes root: {store_id!r}")
        if is_generated_store_artifact(store_id) and not os.path.lexists(final):
            raise ValueError(f"Invalid managed store id: {store_id!r}")
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Could not resolve managed store id {store_id!r}") from exc
    return final


def resolve_auto_store_id(
    root: Path,
    base_id: str,
    family: StoreFamily,
    identity: dict[str, str],
) -> str:
    """Disambiguate an auto-derived id without breaking true reimports."""
    base = resolve_managed_slot(root, base_id)
    if not os.path.lexists(base):
        return base_id
    base_meta = _owned_slot_meta(base, base_id, family)
    if base_meta is None or _identity_matches(base_meta, identity):
        return base_id

    payload = json.dumps(
        {"family": family, "identity": identity},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    for length in (12, 20, 32, 64):
        candidate = f"{base_id}-{digest[:length]}"
        slot = resolve_managed_slot(root, candidate)
        if not os.path.lexists(slot):
            return candidate
        slot_meta = _owned_slot_meta(slot, candidate, family)
        if slot_meta is None or _identity_matches(slot_meta, identity):
            return candidate
    raise ValueError(f"Could not derive an unused managed store id for {base_id!r}")


def _owned_slot_meta(directory: Path, slot_id: str, family: StoreFamily) -> dict[str, str] | None:
    if not _prove_owned_directory(directory, slot_id, family):
        return None
    return _validated_ownership_index_meta(directory / "index.sqlite", family)


def _identity_matches(meta: dict[str, str], identity: dict[str, str]) -> bool:
    """Whether an installed slot is the same source, imported for the same language.

    The language is compared through :func:`meta_language` on BOTH sides, so an
    absent key reads as ``"ja"`` wherever it is missing. That single rule covers
    the three cases:

    * A slot installed before the transition has no ``language`` meta row and a
      Japanese import passes no ``language`` identity key — both normalize to
      ``"ja"`` and the slot is reused, byte for byte as it is today. No existing
      user grows a duplicate.
    * A Chinese import of a source already installed for Japanese mismatches, so
      it forks its own slot instead of relabelling the Japanese one.
    * The reverse order mismatches too: an unstamped Japanese identity is not a
      wildcard, so a Japanese import cannot claim an installed Chinese slot.

    Every other key keeps the plain subset comparison — an identity names only
    the fields it wants to pin, and a slot may carry more.
    """
    if meta_language(meta) != meta_language(identity):
        return False
    return all(meta.get(key) == value for key, value in identity.items() if key != "language")


def is_generated_store_artifact(name: str) -> bool:
    """Return whether *name* is a generated backup/recovery/staging entry."""
    return name.startswith(".") or any(marker in name for marker in (".bak-", ".tomb-", ".corrupt-", ".staging-"))


def write_ownership_marker(directory: Path, slot_id: str, family: StoreFamily) -> None:
    """Mark a staged/generated directory as owned by one managed slot."""
    validate_store_id(slot_id)
    if family not in ("dictionary", "frequency", "audio", "pitch"):
        raise ValueError(f"Unknown managed store family: {family!r}")
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / _OWNERSHIP_MARKER
    marker.write_text(
        json.dumps({"family": family, "slot_id": slot_id}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_ownership_marker(directory: Path) -> tuple[StoreFamily, str] | None:
    """Read a strict ownership marker without following a marker symlink."""
    marker = directory / _OWNERSHIP_MARKER
    try:
        if marker.is_symlink() or not marker.is_file():
            return None
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"family", "slot_id"}:
        return None
    family = payload.get("family")
    slot_id = payload.get("slot_id")
    if family not in ("dictionary", "frequency", "audio", "pitch") or not isinstance(slot_id, str):
        return None
    try:
        validate_store_id(slot_id)
    except ValueError:
        return None
    return family, slot_id


def _supported_schema_version(family: StoreFamily, version: int) -> bool:
    if family == "dictionary":
        from anki_miner.services.dictionary.storage import SCHEMA_VERSION

        return version == SCHEMA_VERSION
    if family == "frequency":
        from anki_miner.services.frequency.storage import SCHEMA_VERSION

        return version == SCHEMA_VERSION
    if family == "pitch":
        from anki_miner.services.pitch_accent.storage import SCHEMA_VERSION

        return version == SCHEMA_VERSION
    from anki_miner.services.audio_packs.storage import SCHEMA_VERSION

    return version == SCHEMA_VERSION


# Oldest dictionary index schema this app ever wrote. Ownership proof accepts
# anything from here up to the current version.
_OLDEST_OWNED_DICT_SCHEMA = 3
_OLDEST_OWNED_FREQUENCY_SCHEMA = 1
_OLDEST_OWNED_PITCH_SCHEMA = 1
_OLDEST_OWNED_AUDIO_SCHEMA = 1


def _supported_ownership_schema_version(family: StoreFamily, version: int) -> bool:
    if family == "dictionary":
        from anki_miner.services.dictionary.storage import SCHEMA_VERSION

        # A RANGE, never a version pair. Ownership answers "did we write this
        # directory", which stays true for every schema we ever wrote; staleness
        # is a separate question already answered by DictMeta.schema_ok. Pinning
        # {oldest, current} silently un-owns the immediately-previous version on
        # every bump — exactly the dictionaries an upgrade needs to repair — so
        # Reimport All would refuse them as missing-source and the user would
        # have to re-add every dictionary by hand.
        return _OLDEST_OWNED_DICT_SCHEMA <= version <= SCHEMA_VERSION
    if family == "frequency":
        from anki_miner.services.frequency.storage import SCHEMA_VERSION

        return _OLDEST_OWNED_FREQUENCY_SCHEMA <= version <= SCHEMA_VERSION
    if family == "pitch":
        from anki_miner.services.pitch_accent.storage import SCHEMA_VERSION

        return _OLDEST_OWNED_PITCH_SCHEMA <= version <= SCHEMA_VERSION
    from anki_miner.services.audio_packs.storage import SCHEMA_VERSION

    return _OLDEST_OWNED_AUDIO_SCHEMA <= version <= SCHEMA_VERSION


def _is_regular_file_nofollow(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _validated_index_meta_with_policy(
    db_path: Path,
    family: StoreFamily,
    supports_version: Callable[[StoreFamily, int], bool],
) -> dict[str, str] | None:
    if family not in ("dictionary", "frequency", "audio", "pitch") or not _is_regular_file_nofollow(db_path):
        return None
    try:
        conn = open_readonly(db_path)
        try:
            meta = {
                key: value
                for key, value in conn.execute("SELECT key, value FROM meta")
                if isinstance(key, str) and isinstance(value, str)
            }
            version = int(meta.get("schema_version", ""))
            if not supports_version(family, version):
                return None
            entry_columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)") if isinstance(row[1], str)}
            if family == "dictionary":
                tag_columns = {row[1] for row in conn.execute("PRAGMA table_info(tags)") if isinstance(row[1], str)}
                if not entry_columns >= _DICTIONARY_ENTRY_COLUMNS:
                    return None
                if not tag_columns >= _DICTIONARY_TAG_COLUMNS:
                    return None
            elif family == "frequency":
                required = _FREQUENCY_V1_COLUMNS if version == 1 else _FREQUENCY_V2_COLUMNS
                if not required <= entry_columns:
                    return None
            elif family == "pitch":
                if not entry_columns >= _PITCH_ENTRY_COLUMNS:
                    return None
            elif not entry_columns >= _AUDIO_ENTRY_COLUMNS:
                return None
            return meta
        finally:
            conn.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None


def _validated_index_meta(db_path: Path, family: StoreFamily) -> dict[str, str] | None:
    return _validated_index_meta_with_policy(db_path, family, _supported_schema_version)


def _validated_ownership_index_meta(db_path: Path, family: StoreFamily) -> dict[str, str] | None:
    return _validated_index_meta_with_policy(db_path, family, _supported_ownership_schema_version)


def validate_index_schema(db_path: Path, family: StoreFamily) -> bool:
    """Validate one family's supported version and queried physical columns."""
    return _validated_index_meta(db_path, family) is not None


def validate_index_schema_cached(db_path: Path, family: StoreFamily) -> bool:
    """Answer :func:`validate_index_schema` from ``meta.json`` where it can.

    The full check costs one SQLite open plus a PRAGMA per store, and startup
    recovery pays it per configured slot before the first window is composed.
    A sidecar no older than its index already carries the meta rows; since
    :func:`write_meta` also records the physical columns there, it carries the
    whole verdict.

    The sidecar is a cache of the answer, never a second policy: every branch in
    :func:`_sidecar_schema_verdict` mirrors the SQLite one, and anything the
    sidecar cannot answer — missing, older than ``index.sqlite``, unparseable, or
    written before columns were recorded — falls through to the full check.
    Slots imported before column recording therefore keep today's behaviour until
    their next reimport republishes the sidecar.

    This is the trust the registries already place in sidecar-derived meta for
    their ``schema_ok`` policy: a sidecar no older than the database is taken as
    that database's meta. It answers the schema question only — corruption that
    leaves the file mtime untouched is as invisible here as it is to the registry
    scan. The file check below stays nofollow and runs first, so a symlinked or
    absent index is refused before the sidecar is read at all.
    """
    if family not in ("dictionary", "frequency", "audio", "pitch") or not _is_regular_file_nofollow(db_path):
        return False
    verdict = _sidecar_schema_verdict(db_path, family)
    if verdict is not None:
        return verdict
    return validate_index_schema(db_path, family)


def _sidecar_schema_verdict(db_path: Path, family: StoreFamily) -> bool | None:
    """Return the verdict a fresh sidecar proves, or ``None`` if it cannot.

    Startup recovery quarantines a slot this returns ``False`` for, so a verdict
    that under-records a healthy index destroys a good store. Freshness here is
    mtime-only and proves no provenance; the false-broken direction is unreachable
    only because of two things this module does not enforce:

    1. Nothing mutates an index in place — no ``ALTER TABLE`` anywhere — so a
       recorded column list can never describe a schema the database has since
       grown out of. An in-place migration would break this the day it lands.
    2. :func:`write_meta` closes its connection *before* publishing the sidecar,
       so the sidecar's mtime is never older than the last byte written to the
       database. Switching these indexes to WAL would break it: the ``-wal`` file
       absorbs the commit and the main database's mtime stops moving with it.

    Either change makes a stale verdict live and destructive. Whoever makes one
    must gate this path on something stronger than an mtime — a content hash, or
    a generation counter written into both files.
    """
    payload = _read_fresh_sidecar(db_path)
    if payload is None:
        return None
    recorded = payload.get(_SIDECAR_COLUMNS_KEY)
    if recorded is None:
        return None
    try:
        columns = json.loads(recorded)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(columns, dict) or any(
        table not in columns
        or not isinstance(columns[table], list)
        or not all(isinstance(name, str) for name in columns[table])
        for table in _SIDECAR_COLUMN_PRAGMAS
    ):
        return None

    # From here the sidecar has proven it can answer, so every exit is a verdict
    # — mirroring _validated_index_meta_with_policy, for which an unreadable
    # schema_version is invalid rather than a reason to look elsewhere.
    try:
        version = int(payload.get("schema_version", ""))
    except ValueError:
        return False
    if not _supported_schema_version(family, version):
        return False
    entry_columns = frozenset(columns["entries"])
    if family == "dictionary":
        return entry_columns >= _DICTIONARY_ENTRY_COLUMNS and frozenset(columns["tags"]) >= _DICTIONARY_TAG_COLUMNS
    if family == "frequency":
        required = _FREQUENCY_V1_COLUMNS if version == 1 else _FREQUENCY_V2_COLUMNS
        return required <= entry_columns
    if family == "pitch":
        return entry_columns >= _PITCH_ENTRY_COLUMNS
    return entry_columns >= _AUDIO_ENTRY_COLUMNS


def _prove_owned_directory(directory: Path, slot_id: str, family: StoreFamily) -> bool:
    if directory.is_symlink() or not directory.is_dir():
        return False
    marker_path = directory / _OWNERSHIP_MARKER
    if os.path.lexists(marker_path):
        return read_ownership_marker(directory) == (family, slot_id)
    meta = _validated_ownership_index_meta(directory / "index.sqlite", family)
    if meta is None:
        return False
    if family == "dictionary":
        return "source_name" in meta and "schema_version" in meta
    if family in ("frequency", "pitch"):
        return "schema_version" in meta
    return meta.get("pack_id") == slot_id


def prove_owned_slot(root: Path, slot_id: str, family: StoreFamily) -> bool:
    """Prove canonical slot ownership by exact marker or legacy physical schema."""
    try:
        slot = resolve_managed_slot(root, slot_id)
    except ValueError:
        return False
    return _prove_owned_directory(slot, slot_id, family)


def prove_owned_generation(
    root: Path,
    slot_id: str,
    family: StoreFamily,
    generation: Path,
) -> bool:
    """Prove one direct generated sibling belongs to *slot_id* and *family*."""
    try:
        resolved_root = root.resolve()
        validate_store_id(slot_id)
        if generation.parent.resolve() != resolved_root:
            return False
    except (OSError, RuntimeError, ValueError):
        return False
    return _prove_owned_directory(generation, slot_id, family)


def readonly_sqlite_uri(db_path: Path) -> str:
    """Build the ``file:`` read-only URI for ``db_path``.

    ``Path.as_uri()`` percent-encodes URI-significant characters (``#``, ``?``,
    ``%``) so they can't truncate the path, but on Windows it renders
    extended-length prefixes (``\\\\?\\C:\\...`` / ``\\\\?\\UNC\\server\\share``)
    as a ``file://%3F/...`` authority that sqlite rejects — silently skipping
    valid stores. Strip the extended-length prefix back to the plain drive/UNC
    form before conversion; sqlite re-applies long-path handling itself.
    """
    resolved = db_path.resolve()
    stripped = _strip_extended_length_prefix(str(resolved))
    if stripped is not None:
        resolved = Path(stripped)
    return resolved.as_uri() + "?mode=ro"


def _strip_extended_length_prefix(raw: str) -> str | None:
    """Return ``raw`` without a Windows extended-length prefix, or None if absent."""
    if raw.startswith("\\\\?\\UNC\\"):
        return "\\\\" + raw[8:]
    if raw.startswith("\\\\?\\"):
        return raw[4:]
    return None


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
    re-opening SQLite when nothing changed, and the physical columns recorded
    alongside let :func:`validate_index_schema_cached` avoid it too.
    """
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        for key, value in items.items():
            stored = value_transform(value) if value_transform is not None else value
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, stored),
            )
        conn.commit()
        full_meta = {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
        columns = {
            table: [row[1] for row in conn.execute(pragma) if isinstance(row[1], str)]
            for table, pragma in _SIDECAR_COLUMN_PRAGMAS.items()
        }
    finally:
        conn.close()
    write_meta_sidecar(db_path, full_meta, sidecar_name=sidecar_name, columns=columns)


def meta_language(meta: dict[str, str]) -> str:
    """The language a slot was imported under; ``"ja"`` when absent.

    Every index written before the multi-language transition has no ``language``
    key, and the tolerant default is what keeps those slots loading unchanged —
    there is no migration and no reimport for them.
    """
    value = meta.get("language")
    return value if isinstance(value, str) and value else "ja"


def read_slot_language(slot_dir: Path, *, sidecar_name: str = _META_SIDECAR) -> str:
    """The language stamp of an installed slot; ``"ja"`` when it cannot be read.

    A repair rebuilds the slot from its persisted source copy, so the language it
    was imported under has to be recovered *before* the rebuild — otherwise a
    repaired Chinese index comes back stamped "ja" and the chain build drops it
    from a Chinese session. Never raises: a slot corrupt enough to need repairing
    is exactly the input this has to survive, and "ja" is the same default an
    unstamped legacy slot already gets.
    """
    try:
        payload = json.loads((slot_dir / sidecar_name).read_text(encoding="utf-8"))
        value = payload.get("language")
        if isinstance(value, str) and value:
            return value
    except (OSError, ValueError, AttributeError):
        pass

    db_path = slot_dir / "index.sqlite"
    if not db_path.is_file():
        return "ja"
    try:
        conn = sqlite3.connect(readonly_sqlite_uri(db_path), uri=True)
    except (OSError, ValueError, sqlite3.Error):
        return "ja"
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'language'").fetchone()
    except sqlite3.Error:
        return "ja"
    finally:
        conn.close()
    return row[0] if row and isinstance(row[0], str) and row[0] else "ja"


class LanguageKwarg(TypedDict, total=False):
    """The ``language=`` keyword bundle an importer call is splatted with."""

    language: str


def language_kwarg(language: str) -> LanguageKwarg:
    """``{"language": language}``, or nothing at all when it is the "ja" default.

    Every importer defaults to ``"ja"``, so a ja call site that spells the
    keyword out and one that omits it are equivalent — and omitting it is what
    keeps the pre-transition call byte-identical all the way down, including
    the test doubles that mirror an importer's exact signature. Splat this
    instead of passing ``language=`` unconditionally.
    """
    return {} if language == "ja" else {"language": language}


def language_identity(language: str) -> dict[str, str]:
    """``{"language": language}`` for a slot-identity dict, empty for ja.

    ``resolve_auto_store_id`` derives a fork id by hashing the identity dict, so
    the Japanese identity has to stay key for key what it was before the
    transition — one extra key would move every already-forked Japanese slot to
    a new id and orphan the installed one. ``_identity_matches`` reads an absent
    key as ``"ja"`` on both sides, so the omission carries the same meaning the
    explicit value would.

    Same omit-when-ja reasoning as :func:`language_kwarg`, kept separate because
    that one is typed as a call-keyword bundle rather than as identity fields.
    """
    return {} if language == "ja" else {"language": language}


def slot_language_kwarg(slot_dir: Path) -> LanguageKwarg:
    """The ``language=`` keyword a rebuild of *slot_dir* has to carry, if any.

    Reimport All (and the android-db re-point) rebuild a slot in place through
    the ordinary *import* path, not the repair path — so the importer stamps its
    own default and a Chinese slot would come back stamped "ja", dropping out of
    a Chinese chain until the user reimported it by hand. Reading the stamp off
    the slot first and replaying it keeps the language across the rebuild,
    exactly as the repair path already does internally.
    """
    return language_kwarg(read_slot_language(slot_dir))


def read_meta(db_path: Path) -> dict[str, str]:
    """Read all ``meta`` rows. Returns an empty dict if the file is missing."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(readonly_sqlite_uri(db_path), uri=True)
    try:
        return {
            key: value
            for key, value in conn.execute("SELECT key, value FROM meta")
            if isinstance(key, str) and isinstance(value, str)
        }
    finally:
        conn.close()


def read_meta_cached(
    db_path: Path,
    read_meta_fn: Callable[[Path], dict[str, str]],
    *,
    sidecar_name: str = _META_SIDECAR,
) -> dict[str, str]:
    """Read ``meta`` rows via the ``meta.json`` sidecar when it is fresh.

    Falls through to ``read_meta_fn`` without publishing a sidecar when:
    * the sidecar is missing,
    * ``index.sqlite`` is newer than the sidecar,
    * the sidecar is unreadable / not valid JSON.

    Only the explicit writer path, :func:`write_meta`, publishes the sidecar;
    reads never repair or refresh it.

    ``read_meta_fn`` is passed in (rather than calling :func:`read_meta`
    directly) so each storage module routes the fall-through through *its own*
    module-level ``read_meta`` — the seam the storage tests patch to assert the
    SQLite open is skipped on the hot startup path.
    """
    if not db_path.exists():
        return {}
    payload = _read_fresh_sidecar(db_path, sidecar_name)
    if payload is not None:
        return {key: value for key, value in payload.items() if key != _SIDECAR_COLUMNS_KEY}
    return read_meta_fn(db_path)


def _read_fresh_sidecar(db_path: Path, sidecar_name: str = _META_SIDECAR) -> dict[str, str] | None:
    """Return the sidecar payload when it is no older than *db_path*, else None.

    The one freshness gate both sidecar readers share — the meta rows
    (:func:`read_meta_cached`) and the schema verdict
    (:func:`validate_index_schema_cached`) must never disagree about whether a
    sidecar may be trusted. The payload is returned raw, reserved keys included.
    """
    sidecar = db_path.parent / sidecar_name
    try:
        # Nanosecond mtimes (not float st_mtime, which truncates to microsecond-
        # ish resolution on some platforms and can round two same-second writes
        # to equal floats): a promoted index that writes its meta.json sidecar
        # in the same wall-clock second the DB itself was written would
        # otherwise look "not older than" the DB and be trusted as fresh even
        # when it is actually stale.
        if not (sidecar.is_file() and sidecar.stat().st_mtime_ns >= db_path.stat().st_mtime_ns):
            return None
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as e:
        logger.debug("meta sidecar miss for %s: %s", db_path, e)
        return None
    if isinstance(data, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        return data
    return None


def write_meta_sidecar(
    db_path: Path,
    meta: dict[str, str],
    *,
    sidecar_name: str = _META_SIDECAR,
    columns: dict[str, list[str]] | None = None,
) -> None:
    """Best-effort sidecar write. Publication failures are logged, not raised.

    Written via :func:`atomic_write_path` (write-to-temp-then-``os.replace``) so
    a crash or exception mid-write can never leave a truncated/partial
    ``meta.json`` next to the promoted, live index — a reader would otherwise
    hit the ``json.JSONDecodeError`` guard in :func:`read_meta_cached` and
    silently fall through to a full re-scan, or worse, briefly observe a
    half-written file that happens to parse as valid but incomplete JSON.

    ``columns`` (the physical column list, keyed by table) is published under the
    reserved ``_SIDECAR_COLUMNS_KEY`` as a JSON string, keeping the payload the
    flat ``str -> str`` map every reader validates. Omitting it publishes a
    sidecar that can serve meta rows but not a schema verdict.
    """
    payload = dict(meta)
    if columns is not None:
        payload[_SIDECAR_COLUMNS_KEY] = json.dumps(columns, sort_keys=True)
    sidecar = db_path.parent / sidecar_name
    try:
        with atomic_write_path(sidecar) as tmp:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
    except (OSError, TypeError, ValueError, RecursionError) as e:  # pragma: no cover - defensive
        logger.debug("Failed to write meta sidecar %s: %s", sidecar, e)


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a read-only connection. Safe to share across threads.

    ``check_same_thread=False`` is required because providers/fetchers are
    constructed on the GUI thread (by service_factory) but consumed by worker
    threads. The connection is read-only (``PRAGMA query_only=ON``) so concurrent
    reads are safe under sqlite3's serialized access mode.
    """
    conn = sqlite3.connect(readonly_sqlite_uri(db_path), uri=True, check_same_thread=False)
    try:
        conn.execute("PRAGMA query_only=ON")
    except Exception:
        conn.close()
        raise
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
    under ``child.name`` (a ``None`` return means "skip this child").

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
        if child_prefilter is None:
            if is_generated_store_artifact(child.name):
                continue
        elif not child_prefilter(child):
            continue
        db = child / "index.sqlite"
        if not _is_regular_file_nofollow(db):
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

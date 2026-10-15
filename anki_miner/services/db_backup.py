"""Daily rotated backups of the user's own data, and a restore at the next launch (WP-13).

What is copied (R9): every mining language's known-words database, the stats
database, ``gui_config.json`` and the settings profiles. Anki-derived data that
can be rebuilt (dictionaries, frequency lists, caches) is not.

One folder per day under ``backups/``. A day is written into a hidden staging
folder and renamed into place once complete, so a crash leaves only a staging
folder, which the next backup removes. ``manifest.json`` maps each numbered
copy to the absolute path it came from; the restore puts it back there, so it
needs no config, which matters because it runs before the config is read.

A restore is never applied to live files: Settings writes ``restore-pending.json``
and the next launch applies it before any database is opened (``gui.app``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

BACKUPS_DIRNAME = "backups"
#: Days kept (R9).
KEEP_DAYS = 7
MANIFEST_NAME = "manifest.json"
PENDING_RESTORE_NAME = "restore-pending.json"
_STAGING_PREFIX = ".staging-"
_SQLITE_HEADER = b"SQLite format 3\x00"
#: Files SQLite keeps beside a database. A stale one would be replayed onto
#: the restored file, so a restore removes them.
_SQLITE_SIDECARS = ("-journal", "-wal", "-shm")
_KINDS = frozenset({"sqlite", "file"})


@dataclass(frozen=True)
class BackupEntry:
    """One copied file: its name in the day folder and where it came from."""

    name: str
    source: Path
    kind: str


@dataclass(frozen=True)
class BackupDay:
    """One complete day folder."""

    day: date
    path: Path
    entries: tuple[BackupEntry, ...]


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
    except OSError:
        return False


def _copy_sqlite(source: Path, target: Path) -> None:
    """A consistent copy through SQLite's online backup, even while another connection is open."""
    src = sqlite3.connect(str(source))
    try:
        src.execute("PRAGMA busy_timeout = 5000")
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if os.name == "posix":
        os.chmod(target, 0o600)


def _copy_file(source: Path, target: Path) -> None:
    shutil.copyfile(source, target)
    # gui_config.json can carry an AnkiConnect key (AI-02): owner-only.
    if os.name == "posix":
        os.chmod(target, 0o600)


def _remove_staging(root: Path) -> None:
    """Drop staging folders a crashed backup left behind."""
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_dir() and child.name.startswith(_STAGING_PREFIX):
            shutil.rmtree(child, ignore_errors=True)


def run_daily_backup(
    sources: Sequence[Path],
    root: Path,
    *,
    today: date | None = None,
    keep: int = KEEP_DAYS,
) -> Path | None:
    """Back up ``sources`` into ``root/<today>/`` unless today's backup exists; keep ``keep`` days.

    A source that cannot be read is skipped with a warning; the rest are still
    copied. Returns the new day folder, or ``None`` when today's was already
    complete.
    """
    day = today or date.today()
    dest = root / day.isoformat()
    if (dest / MANIFEST_NAME).is_file():
        return None
    root.mkdir(parents=True, exist_ok=True)
    _remove_staging(root)
    # mkdtemp creates the folder 0700: the copies include the settings.
    stage = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=root))
    files: list[dict[str, str]] = []
    try:
        for index, source in enumerate(sources, start=1):
            name = f"{index:02d}-{source.name}"
            kind = "sqlite" if _is_sqlite(source) else "file"
            try:
                if kind == "sqlite":
                    _copy_sqlite(source, stage / name)
                else:
                    _copy_file(source, stage / name)
            except (OSError, sqlite3.Error) as exc:
                logger.warning("Backup skipped %s: %s", source, exc)
                with contextlib.suppress(OSError):
                    (stage / name).unlink(missing_ok=True)
                continue
            files.append({"name": name, "source": str(source.resolve()), "kind": kind})
        manifest = {
            "version": 1,
            "day": day.isoformat(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "files": files,
        }
        (stage / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if dest.exists():
            # A day folder without a manifest is a half-written leftover.
            shutil.rmtree(dest)
        os.replace(stage, dest)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    pruned = prune_backups(root, keep=keep)
    log_summary(logger, "Daily backup done", day=day.isoformat(), files=len(files), pruned=pruned)
    return dest


def _read_manifest(folder: Path) -> tuple[BackupEntry, ...] | None:
    """The folder's entries, or ``None`` when its manifest is missing, corrupt or names a file it lacks."""
    try:
        payload = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list):
        return None
    entries: list[BackupEntry] = []
    for item in files:
        if not isinstance(item, dict):
            return None
        name, source, kind = item.get("name"), item.get("source"), item.get("kind")
        if not (isinstance(name, str) and isinstance(source, str) and kind in _KINDS):
            return None
        # A bare file name inside this folder, nothing else.
        if name != Path(name).name or name.startswith(".") or not (folder / name).is_file():
            return None
        entries.append(BackupEntry(name=name, source=Path(source), kind=str(kind)))
    return tuple(entries)


def list_backups(root: Path) -> list[BackupDay]:
    """Every complete day folder under ``root``, newest first."""
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    days: list[BackupDay] = []
    for child in children:
        if not child.is_dir():
            continue
        try:
            day = date.fromisoformat(child.name)
        except ValueError:
            continue
        entries = _read_manifest(child)
        if entries is None:
            continue
        days.append(BackupDay(day=day, path=child, entries=entries))
    days.sort(key=lambda backup: backup.day, reverse=True)
    return days


def prune_backups(root: Path, *, keep: int = KEEP_DAYS) -> int:
    """Delete all but the ``keep`` newest days; return how many went."""
    removed = 0
    for old in list_backups(root)[keep:]:
        shutil.rmtree(old.path, ignore_errors=True)
        removed += 1
    return removed

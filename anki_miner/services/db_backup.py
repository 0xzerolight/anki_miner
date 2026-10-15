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
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.logging_ext import log_summary

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anki_miner.config import AnkiMinerConfig

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


def schedule_restore(root: Path, day: date) -> None:
    """Ask the next launch to restore ``day``'s backup (Settings → General).

    Raises:
        ValueError: There is no complete backup for ``day``.
        OSError: The request could not be written.
    """
    if not any(backup.day == day for backup in list_backups(root)):
        raise ValueError(f"no backup for {day.isoformat()}")
    root.mkdir(parents=True, exist_ok=True)
    with atomic_write_path(root / PENDING_RESTORE_NAME) as staged:
        staged.write_text(json.dumps({"day": day.isoformat()}), encoding="utf-8")


def pending_restore(root: Path) -> date | None:
    """The day a restore is scheduled for, or ``None`` (absent or corrupt request)."""
    try:
        payload = json.loads((root / PENDING_RESTORE_NAME).read_text(encoding="utf-8"))
        return date.fromisoformat(payload["day"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _restore_entry(folder: Path, entry: BackupEntry) -> None:
    """Put one copy back over its source path, atomically."""
    target = entry.source
    target.parent.mkdir(parents=True, exist_ok=True)
    if entry.kind == "sqlite":
        for suffix in _SQLITE_SIDECARS:
            Path(f"{target}{suffix}").unlink(missing_ok=True)
    with atomic_write_path(target) as staged:
        shutil.copyfile(folder / entry.name, staged)
        if os.name == "posix" and entry.kind == "file":
            os.chmod(staged, 0o600)


def apply_pending_restore(root: Path) -> int | None:
    """Apply a scheduled restore; return the files restored, or ``None`` when none was scheduled.

    Must run before anything opens these files: ``gui.app`` calls it ahead of
    the config load, holding the instance and run locks. The request is
    removed whatever happens, so a restore that fails is reported once in the
    log and never turns into a boot loop.
    """
    pending_path = root / PENDING_RESTORE_NAME
    day = pending_restore(root)
    if day is None:
        if pending_path.exists():
            logger.warning("Backup restore request unreadable; dropping it: %s", pending_path)
            with contextlib.suppress(OSError):
                pending_path.unlink()
        return None
    restored = 0
    try:
        backup = next((candidate for candidate in list_backups(root) if candidate.day == day), None)
        if backup is None:
            logger.warning("Backup restore skipped: no complete backup for %s", day.isoformat())
            return 0
        for entry in backup.entries:
            try:
                _restore_entry(backup.path, entry)
            except OSError as exc:
                logger.warning("Backup restore of %s failed: %s", entry.source, exc)
                continue
            restored += 1
        log_summary(logger, "Backup restored", day=day.isoformat(), files=restored, of=len(backup.entries))
        return restored
    finally:
        with contextlib.suppress(OSError):
            pending_path.unlink(missing_ok=True)


def backups_root(config_file: Path) -> Path:
    """The backups folder: beside ``gui_config.json`` in the data folder.

    Derived from the config file the caller names (``GUIConfigManager.CONFIG_FILE``)
    rather than from ``ANKI_MINER_HOME`` at import, the way ``ProfileStore``
    finds its folder, so an isolated test home is honoured.
    """
    return config_file.parent / BACKUPS_DIRNAME


def backup_sources(config: AnkiMinerConfig, *, config_file: Path, profiles_dir: Path) -> list[Path]:
    """The files a daily backup copies (R9), existing ones only.

    Every mining language's known-words database, the stats database,
    ``gui_config.json`` and each settings profile. Language paths come from
    ``resolve_known_words_db_path``, their sole derivation, so a moved
    known-words file is followed.
    """
    # Function-local: the derivation lives in the GUI composition module,
    # which imports most services (this one included, through gui.app).
    from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
    from anki_miner.languages import AVAILABLE_LANGUAGES

    candidates = [resolve_known_words_db_path(replace(config, language=code)) for code in AVAILABLE_LANGUAGES]
    candidates += [config.stats_db_path, config_file]
    try:
        candidates += sorted(profiles_dir.glob("*.json"))
    except OSError as exc:
        logger.warning("Backup: could not list settings profiles in %s: %s", profiles_dir, exc)
    sources: list[Path] = []
    for path in candidates:
        if path not in sources and path.is_file():
            sources.append(path)
    return sources

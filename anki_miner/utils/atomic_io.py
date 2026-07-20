"""Atomic file writes and crash-recoverable directory replacement."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_write_path(dest: Path) -> Iterator[Path]:
    """Yield a unique sibling temp path, then atomically replace *dest*."""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.stem}-", suffix=dest.suffix, dir=dest.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        yield tmp
        os.replace(tmp, dest)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def atomic_replace_dir(new_dir: Path, dest_dir: Path) -> None:
    """Promote *new_dir*, retaining and restoring any old *dest_dir* on fault."""
    if not new_dir.is_dir():
        raise NotADirectoryError(new_dir)

    reconcile_dir(dest_dir)
    backup: Path | None = None
    if dest_dir.exists():
        if not dest_dir.is_dir():
            raise NotADirectoryError(dest_dir)
        backup = _unique_backup_path(dest_dir)
        os.replace(dest_dir, backup)

    try:
        os.replace(new_dir, dest_dir)
    except BaseException:
        if backup is not None:
            _restore_backup(backup, dest_dir)
        raise
    else:
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)


def reconcile_dir(dest_dir: Path) -> None:
    """Restore newest non-empty ``.bak-*`` sibling when *dest_dir* is absent or empty."""
    if _is_valid_dir(dest_dir):
        return

    prefix = dest_dir.name + ".bak-"
    try:
        backups = sorted(
            (child for child in dest_dir.parent.iterdir() if child.name.startswith(prefix) and _is_valid_dir(child)),
            key=lambda child: child.name,
            reverse=True,
        )
    except OSError:
        return
    if not backups:
        return

    if dest_dir.exists():
        try:
            dest_dir.rmdir()
        except OSError:
            return
    try:
        os.replace(backups[0], dest_dir)
    except OSError:
        return


def reconcile_backups_in(root: Path) -> None:
    """Reconcile every direct ``X.bak-*`` child of *root* back to ``X``."""
    try:
        canonical_names = {
            canonical
            for child in root.iterdir()
            for canonical, marker, suffix in (child.name.rpartition(".bak-"),)
            if marker and canonical and suffix
        }
    except OSError:
        return

    for canonical_name in sorted(canonical_names):
        try:
            reconcile_dir(root / canonical_name)
        except OSError:
            continue


def _is_valid_dir(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def _restore_backup(backup: Path, dest_dir: Path) -> None:
    if _is_valid_dir(dest_dir):
        return
    if dest_dir.exists():
        try:
            dest_dir.rmdir()
        except OSError:
            return
    try:
        os.replace(backup, dest_dir)
    except OSError:
        return


def _unique_backup_path(dest_dir: Path) -> Path:
    while True:
        backup = dest_dir.with_name(f"{dest_dir.name}.bak-{time.time_ns()}-{uuid.uuid4().hex}")
        if not backup.exists():
            return backup

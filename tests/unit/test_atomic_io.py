from __future__ import annotations

import os
from pathlib import Path

import pytest

import anki_miner.utils.atomic_io as atomic_io
from anki_miner.utils.atomic_io import atomic_replace_dir, atomic_write_path, reconcile_dir


def test_atomic_write_path_fault_preserves_existing_file(tmp_path: Path) -> None:
    dest = tmp_path / "output.txt"
    dest.write_bytes(b"good")

    with pytest.raises(OSError, match="write fault"), atomic_write_path(dest) as staged:
        staged.write_bytes(b"partial")
        raise OSError("write fault")

    assert dest.read_bytes() == b"good"
    assert sorted(child.name for child in tmp_path.iterdir()) == [dest.name]


def test_atomic_write_path_supports_near_limit_destination_name(tmp_path: Path) -> None:
    dest = tmp_path / ("x" * 250 + ".txt")
    dest.write_bytes(b"old")

    with atomic_write_path(dest) as staged:
        staged.write_bytes(b"new")

    assert dest.read_bytes() == b"new"
    assert sorted(child.name for child in tmp_path.iterdir()) == [dest.name]


def test_atomic_replace_dir_fault_restores_old_target(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "resource"
    dest.mkdir()
    (dest / "payload").write_bytes(b"old")
    staged = tmp_path / ".staging-resource"
    staged.mkdir()
    (staged / "payload").write_bytes(b"new")

    import anki_miner.utils.atomic_io as atomic_io

    real_replace = atomic_io.os.replace

    def fail_promotion(src, dst):
        if Path(src) == staged and Path(dst) == dest:
            raise OSError("promotion fault")
        return real_replace(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", fail_promotion)

    with pytest.raises(OSError, match="promotion fault"):
        atomic_replace_dir(staged, dest)

    assert (dest / "payload").read_bytes() == b"old"
    assert list(tmp_path.glob("resource.bak-*")) == []


def test_atomic_replace_dir_fault_restores_exact_target_not_stale_backup(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "resource"
    dest.mkdir()
    (dest / "payload").write_bytes(b"old")
    stale = tmp_path / "resource.bak-9999999999999999999"
    stale.mkdir()
    (stale / "payload").write_bytes(b"stale")
    staged = tmp_path / ".staging-resource"
    staged.mkdir()
    (staged / "payload").write_bytes(b"new")

    import anki_miner.utils.atomic_io as atomic_io

    real_replace = atomic_io.os.replace

    def fail_promotion(src, dst):
        if Path(src) == staged and Path(dst) == dest:
            raise OSError("promotion fault")
        return real_replace(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", fail_promotion)

    with pytest.raises(OSError, match="promotion fault"):
        atomic_replace_dir(staged, dest)

    assert (dest / "payload").read_bytes() == b"old"
    assert (stale / "payload").read_bytes() == b"stale"


def test_atomic_replace_success_is_not_reversed_by_backup_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dest = tmp_path / "resource"
    dest.mkdir()
    (dest / "payload").write_bytes(b"old")
    staged = tmp_path / ".staging-resource"
    staged.mkdir()
    (staged / "payload").write_bytes(b"new")
    cleanup_error = OSError("cleanup locked")
    cleanup_modes: list[str] = []

    def failed_cleanup(_path: Path, *, mode: str) -> tuple[bool, OSError]:
        cleanup_modes.append(mode)
        return False, cleanup_error

    monkeypatch.setattr(atomic_io, "robust_rmtree", failed_cleanup)

    atomic_replace_dir(staged, dest)

    assert (dest / "payload").read_bytes() == b"new"
    assert cleanup_modes == ["outcome"]
    assert len(list(tmp_path.glob("resource.bak-*"))) == 1


def test_reconcile_dir_restores_newest_valid_backup(tmp_path: Path) -> None:
    dest = tmp_path / "resource"
    dest.mkdir()
    older = tmp_path / "resource.bak-20260721000000000001"
    newer = tmp_path / "resource.bak-20260721000000000002"
    invalid = tmp_path / "resource.bak-20260721000000000003"
    for backup, payload in ((older, b"older"), (newer, b"newer")):
        backup.mkdir()
        (backup / "payload").write_bytes(payload)
    invalid.mkdir()

    reconcile_dir(dest)
    reconcile_dir(dest)

    assert (dest / "payload").read_bytes() == b"newer"
    assert older.is_dir()
    assert invalid.is_dir()


@pytest.mark.parametrize("scan_root", [False, True], ids=["direct", "root-scan"])
def test_reconcile_restores_newest_backup_by_mtime_across_name_formats(tmp_path: Path, scan_root: bool) -> None:
    dest = tmp_path / "resource"
    dest.mkdir()
    newer = tmp_path / "resource.bak-1700000000000000000-epoch"
    older = tmp_path / "resource.bak-2026-07-21T00:00:00-legacy"
    for backup, payload in ((newer, b"newer"), (older, b"older")):
        backup.mkdir()
        (backup / "payload").write_bytes(payload)
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))

    if scan_root:
        atomic_io.reconcile_backups_in(tmp_path)
    else:
        reconcile_dir(dest)

    assert (dest / "payload").read_bytes() == b"newer"


def test_non_sqlite_crash_mid_promote_recovered_by_default_reconciler(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    canonical = raw_root / "X"
    canonical.mkdir(parents=True)
    backup = raw_root / "X.bak-20260721000000000001"
    backup.mkdir()
    (backup / "payload").write_bytes(b"survived")

    atomic_io.reconcile_backups_in(raw_root)
    atomic_io.reconcile_backups_in(raw_root)

    assert (canonical / "payload").read_bytes() == b"survived"


def test_reconcile_backups_in_continues_after_entry_oserror(tmp_path: Path, monkeypatch) -> None:
    for name in ("first", "second"):
        backup = tmp_path / f"{name}.bak-20260721000000000001"
        backup.mkdir()
        (backup / "payload").write_bytes(name.encode())

    real_reconcile = atomic_io.reconcile_dir

    def reconcile_with_fault(dest: Path) -> None:
        if dest.name == "first":
            raise OSError("entry fault")
        real_reconcile(dest)

    monkeypatch.setattr(atomic_io, "reconcile_dir", reconcile_with_fault)

    atomic_io.reconcile_backups_in(tmp_path)

    assert not (tmp_path / "first").exists()
    assert (tmp_path / "second" / "payload").read_bytes() == b"second"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_atomic_write_path_new_file_gets_the_umask_mode(tmp_path: Path) -> None:
    """User outputs (generated SRT, condensed audio, bundles) must be readable the
    way a plain open() file is, e.g. by a media server running as another user;
    mkstemp's 0600 must not leak into the published file."""
    umask = os.umask(0o022)
    os.umask(umask)
    dest = tmp_path / "episode.srt"

    with atomic_write_path(dest) as staged:
        staged.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")

    assert (dest.stat().st_mode & 0o777) == (0o666 & ~umask)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_atomic_write_path_replacement_keeps_the_existing_mode(tmp_path: Path) -> None:
    dest = tmp_path / "episode.srt"
    dest.write_text("old", encoding="utf-8")
    dest.chmod(0o640)

    with atomic_write_path(dest) as staged:
        staged.write_text("new", encoding="utf-8")

    assert dest.read_text(encoding="utf-8") == "new"
    assert (dest.stat().st_mode & 0o777) == 0o640


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_atomic_write_path_overwrites_a_read_only_destination(tmp_path: Path) -> None:
    """Copying the destination's 0444 onto the staged file must not stop the caller
    writing it: replacing a read-only file needs only directory write access."""
    dest = tmp_path / "episode.srt"
    dest.write_text("old", encoding="utf-8")
    dest.chmod(0o444)

    with atomic_write_path(dest) as staged:
        staged.write_text("new", encoding="utf-8")

    assert dest.read_text(encoding="utf-8") == "new"
    assert (dest.stat().st_mode & 0o777) == 0o644

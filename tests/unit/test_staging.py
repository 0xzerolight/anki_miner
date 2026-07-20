from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

import pytest

from anki_miner.services._staging import promote_staged_dir


def test_promote_staged_dir_is_crash_safe(tmp_path: Path, monkeypatch) -> None:
    final = tmp_path / "resource"
    final.mkdir()
    (final / "payload").write_bytes(b"old")
    staging = tmp_path / ".staging-resource"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    real_replace = os.replace

    def crash_during_promotion(src, dst):
        if Path(src) == staging and Path(dst) == final:
            raise KeyboardInterrupt
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", crash_during_promotion)

    with pytest.raises(KeyboardInterrupt):
        promote_staged_dir(staging, final, mover=os.replace, overwrite=True)

    assert (final / "payload").read_bytes() == b"old"


def test_promote_staged_dir_falls_back_on_cross_filesystem_move(tmp_path: Path, monkeypatch) -> None:
    final = tmp_path / "resource"
    final.mkdir()
    (final / "payload").write_bytes(b"old")
    staging = tmp_path / "system-temp-staging"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    real_replace = os.replace

    def cross_filesystem_promotion(src, dst):
        if Path(src) == staging and Path(dst) == final:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", cross_filesystem_promotion)

    promote_staged_dir(staging, final, mover=shutil.move, overwrite=True)

    assert (final / "payload").read_bytes() == b"new"
    assert not staging.exists()
    assert list(tmp_path.glob("resource.bak-*")) == []

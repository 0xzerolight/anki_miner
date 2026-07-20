from __future__ import annotations

import os
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

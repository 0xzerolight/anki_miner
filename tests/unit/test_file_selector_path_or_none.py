"""Tests: FileSelector.path_or_none() preserves whitespace-bearing real paths.

Regression guard for the trailing-space media-folder core dump: the batch tab
validated the raw (un-stripped) path via is_valid() but then handed a stripped
path to the filesystem, so a directory whose name legitimately ends in a space
became a nonexistent path -> FileNotFoundError -> abort. path_or_none() returns
the raw text (never stripped) while still reporting None for empty/whitespace.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from anki_miner.gui.widgets.enhanced.file_selector import FileSelector


def _make_selector(qtbot, **kwargs) -> FileSelector:
    w = FileSelector(**kwargs)
    qtbot.addWidget(w)
    return w


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows strips trailing spaces from file/dir names, so the OS can't hold one.",
)
def test_trailing_space_dir_round_trips(qtbot, tmp_path):
    """A real dir whose name ends in a space validates AND survives path_or_none()."""
    d = tmp_path / "Season 02 "  # trailing space is part of the real name
    d.mkdir()
    assert d.is_dir()  # sanity: the OS kept the trailing space

    w = _make_selector(qtbot, file_mode=False)
    w.set_path(str(d))

    assert w.is_valid() is True
    raw = w.path_or_none()
    assert raw is not None
    assert raw == str(d)  # NOT stripped
    assert Path(raw).is_dir()  # the would-have-core-dumped assertion


def test_leading_space_preserved(qtbot):
    """Leading spaces (legal even on Windows) must survive untouched.

    No filesystem here: Path(" x") is *relative*, so this documents that the
    is_valid() gate — not path_or_none() — is what keeps leading-space input safe
    at the real call sites.
    """
    w = _make_selector(qtbot, file_mode=False)
    w.set_path("  /some/leading space")
    assert w.path_or_none() == "  /some/leading space"


def test_none_when_empty(qtbot):
    w = _make_selector(qtbot, file_mode=False)
    assert w.path_or_none() is None


def test_none_when_whitespace_only(qtbot):
    w = _make_selector(qtbot, file_mode=False)
    w.set_path("   ")
    assert w.path_or_none() is None

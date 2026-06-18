"""Tests for the Windows console-hiding subprocess helper (Issue #79)."""

import pytest

from anki_miner.utils.subprocess_utils import _CREATE_NO_WINDOW, no_window_kwargs


def test_create_no_window_constant_value():
    # subprocess.CREATE_NO_WINDOW on Windows; documented numeric fallback elsewhere.
    assert _CREATE_NO_WINDOW == 0x08000000


@pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd", ""])
def test_no_kwargs_off_windows(monkeypatch, platform):
    monkeypatch.setattr("anki_miner.utils.subprocess_utils.sys.platform", platform)
    assert no_window_kwargs() == {}


def test_creationflags_on_windows(monkeypatch):
    monkeypatch.setattr("anki_miner.utils.subprocess_utils.sys.platform", "win32")
    assert no_window_kwargs() == {"creationflags": _CREATE_NO_WINDOW}

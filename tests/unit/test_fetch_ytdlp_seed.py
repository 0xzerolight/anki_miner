"""Tests for scripts/fetch_ytdlp_seed.py (the bundle smoke's yt-dlp seed).

No network: the updater's installer is replaced, and only the URL the script
would fetch and its failure policy are under test.
"""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_ytdlp_seed.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("fetch_ytdlp_seed", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeUpdater:
    calls: list[tuple[str, str]] = []
    raises: BaseException | None = None
    installed: Path | None = None

    def __init__(self, config: object) -> None:
        self.config = config

    def _download_and_install(self, url: str, version: str) -> Path:
        type(self).calls.append((url, version))
        if type(self).raises is not None:
            raise type(self).raises
        # A REAL file: the script stats what it returns, and monkeypatching
        # Path.stat class-wide breaks every other caller on 3.12+.
        assert type(self).installed is not None
        type(self).installed.parent.mkdir(parents=True, exist_ok=True)
        type(self).installed.write_bytes(b"binary")
        return type(self).installed


@pytest.fixture
def script(monkeypatch, tmp_path):
    module = _load_script()
    monkeypatch.setattr(sys, "platform", "linux")
    _FakeUpdater.calls = []
    _FakeUpdater.raises = None
    _FakeUpdater.installed = tmp_path / "seed" / "bin" / "yt-dlp"
    monkeypatch.setattr(module, "_updater_factory", lambda: _FakeUpdater(object()))
    return module


def test_seeds_the_pinned_release_asset(script, tmp_path):
    assert script.seed(tmp_path / "seed") == 0
    url, version = _FakeUpdater.calls[0]
    assert version == script.PINNED_TAG
    assert url == f"https://github.com/yt-dlp/yt-dlp/releases/download/{script.PINNED_TAG}/yt-dlp_linux"


def test_a_download_outage_warns_and_exits_zero(script, tmp_path, capsys):
    _FakeUpdater.raises = urllib.error.URLError("no route to host")
    assert script.seed(tmp_path / "seed") == 0
    assert "::warning::" in capsys.readouterr().out


def test_a_missing_asset_fails_closed(script, tmp_path, capsys):
    """A 404 means the pin or the asset name is wrong, not that GitHub is down.

    HTTPError subclasses URLError, so without an explicit branch this would warn
    and exit 0 — and every release after it would skip the youtube leg in silence.
    """
    _FakeUpdater.raises = urllib.error.HTTPError(
        "https://github.com/yt-dlp/yt-dlp/releases/download/x/yt-dlp_linux", 404, "Not Found", {}, None
    )
    assert script.seed(tmp_path / "seed") == 1
    assert "::error::" in capsys.readouterr().out


def test_a_server_error_warns_and_exits_zero(script, tmp_path, capsys):
    _FakeUpdater.raises = urllib.error.HTTPError(
        "https://github.com/yt-dlp/yt-dlp/releases/download/x/yt-dlp_linux", 503, "Service Unavailable", {}, None
    )
    assert script.seed(tmp_path / "seed") == 0
    assert "::warning::" in capsys.readouterr().out


def test_a_truncated_transfer_warns_and_exits_zero(script, tmp_path, capsys):
    """http.client.HTTPException is not an OSError, so it needs naming."""
    import http.client

    _FakeUpdater.raises = http.client.IncompleteRead(b"partial")
    assert script.seed(tmp_path / "seed") == 0
    assert "::warning::" in capsys.readouterr().out


def test_a_digest_mismatch_fails_closed(script, tmp_path, capsys):
    _FakeUpdater.raises = ValueError("Downloaded yt-dlp SHA-256 does not match SHA2-256SUMS")
    assert script.seed(tmp_path / "seed") == 1
    assert "::error::" in capsys.readouterr().out


def test_an_unsupported_platform_fails_closed(script, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "sunos5")
    assert script.seed(tmp_path / "seed") == 1
    assert "::error::" in capsys.readouterr().out

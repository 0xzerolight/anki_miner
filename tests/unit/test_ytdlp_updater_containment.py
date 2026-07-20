"""Security containment tests for the app-managed yt-dlp updater (048).

Every response is an in-memory fake.  These tests must never contact GitHub.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QTimer

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.main_window import MainWindow
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.services import ytdlp_updater
from anki_miner.services.ytdlp_updater import YtdlpUpdater
from anki_miner.utils import ytdlp_resolver

_ORIGINAL_MAYBE_START_YTDLP_UPDATE = MainWindow._maybe_start_ytdlp_update
_TAG = "2026.07.20"
_ASSET_NAME = "yt-dlp"
_ASSET_URL = f"https://github.com/yt-dlp/yt-dlp/releases/download/{_TAG}/{_ASSET_NAME}"
_SUMS_URL = f"https://github.com/yt-dlp/yt-dlp/releases/download/{_TAG}/SHA2-256SUMS"
_ALLOWED_FINAL_URL = "https://objects.githubusercontent.com/yt-dlp-release-asset"


class _FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, final_url: str) -> None:
        super().__init__(body)
        self._final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False

    def geturl(self) -> str:
        return self._final_url


@pytest.fixture
def isolated_ytdlp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "anki-home"
    home.mkdir()
    monkeypatch.setenv("ANKI_MINER_HOME", str(home))
    monkeypatch.setattr(ytdlp_updater.paths, "ANKI_MINER_HOME", home)
    monkeypatch.setattr(ytdlp_resolver.paths, "ANKI_MINER_HOME", home)
    monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
    monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
    monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
    return home


def _request_url(request: object) -> str:
    return str(getattr(request, "full_url", request))


def _install_network(
    monkeypatch: pytest.MonkeyPatch,
    binary: bytes,
    *,
    manifest: bytes | None,
    binary_final_url: str = _ALLOWED_FINAL_URL,
    manifest_final_url: str = _ALLOWED_FINAL_URL,
) -> None:
    def fake_urlopen(request: object, timeout: int | None = None):  # noqa: ARG001
        url = _request_url(request)
        if url == _ASSET_URL:
            return _FakeResponse(binary, binary_final_url)
        if url == _SUMS_URL:
            if manifest is None:
                raise OSError("SHA2-256SUMS missing")
            return _FakeResponse(manifest, manifest_final_url)
        raise AssertionError(f"unexpected network request: {url}")

    monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", fake_urlopen)


@pytest.mark.parametrize(
    "manifest",
    [
        pytest.param(None, id="missing_sums"),
        pytest.param(b"0" * 64 + b"  another-file\n", id="missing_entry"),
        pytest.param((b"0" * 64 + b"  yt-dlp\n") * 2, id="duplicate_entry"),
        pytest.param(b"0" * 64 + b"  yt-dlp\n", id="wrong_hash"),
    ],
)
def test_unverified_ytdlp_asset_never_installs_or_executes(
    manifest: bytes | None,
    isolated_ytdlp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = b"untrusted-binary" * 80_000
    _install_network(monkeypatch, binary, manifest=manifest)

    chmod_calls: list[Path] = []
    replace_calls: list[Path] = []
    real_chmod = os.chmod
    real_replace = YtdlpUpdater._atomic_replace

    def spy_chmod(path: str | os.PathLike[str], mode: int) -> None:
        chmod_calls.append(Path(path))
        real_chmod(path, mode)

    def spy_replace(tmp: Path, final: Path) -> None:
        replace_calls.append(final)
        real_replace(tmp, final)

    monkeypatch.setattr(ytdlp_updater.os, "chmod", spy_chmod)
    monkeypatch.setattr(YtdlpUpdater, "_atomic_replace", staticmethod(spy_replace))
    run_commands: list[list[str]] = []

    class _FailedProcess:
        returncode = 1
        stdout = ""

    def fake_run(command: list[str], *args, **kwargs):
        run_commands.append(command)
        return _FailedProcess()

    monkeypatch.setattr(
        ytdlp_updater.subprocess,
        "run",
        fake_run,
    )
    monkeypatch.setattr(shutil, "which", lambda name: None)

    updater = YtdlpUpdater(AnkiMinerConfig())
    with pytest.raises((OSError, ValueError)):
        updater._download_and_install(_ASSET_URL, _TAG)

    final = isolated_ytdlp_home / "bin" / "yt-dlp"
    assert chmod_calls == []
    assert replace_calls == []
    assert not final.exists()
    assert not final.with_name("yt-dlp.verified").exists()
    ytdlp_resolver._clear_cache()
    assert ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig()) != str(final)
    updater.local_version()
    assert all(command[0] != str(final) for command in run_commands)


def test_default_startup_starts_no_downloader(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduled: list[tuple[int, object]] = []
    starts: list[object] = []
    config = AnkiMinerConfig()
    window = SimpleNamespace(
        config=config,
        background_tasks=SimpleNamespace(start_ytdlp_update=lambda *args, **kwargs: starts.append((args, kwargs))),
    )
    monkeypatch.setattr(QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))

    _ORIGINAL_MAYBE_START_YTDLP_UPDATE(window)

    assert config.auto_update_ytdlp is False
    assert scheduled == []
    assert starts == []


def test_existing_config_migrated_to_updater_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", config_path)

    config_path.write_text(
        json.dumps(
            {
                "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION - 1,
                "auto_update_ytdlp": True,
            }
        ),
        encoding="utf-8",
    )
    assert GUIConfigManager.load_config().auto_update_ytdlp is False

    config_path.write_text(
        json.dumps(
            {
                "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
                "auto_update_ytdlp": True,
            }
        ),
        encoding="utf-8",
    )
    assert GUIConfigManager.load_config().auto_update_ytdlp is True


def test_resolver_skips_unverified_managed_binary_and_prefers_path(
    isolated_ytdlp_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = isolated_ytdlp_home / "bin" / "yt-dlp"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"managed")
    managed.chmod(0o755)
    # Even if the managed slot itself appears on PATH, PATH precedence must not
    # launder a receiptless app download into a trusted executable.
    monkeypatch.setattr(shutil, "which", lambda name: str(managed))

    assert ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig()) == "yt-dlp"

    receipt = managed.with_name("yt-dlp.verified")
    receipt.write_text(hashlib.sha256(managed.read_bytes()).hexdigest(), encoding="ascii")
    ytdlp_resolver._clear_cache()
    assert ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig()) == str(managed)

    managed.write_bytes(b"tampered")
    assert ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig()) == "yt-dlp"

    receipt.write_text(hashlib.sha256(managed.read_bytes()).hexdigest(), encoding="ascii")
    path_binary = tmp_path / "path-bin" / "yt-dlp"
    path_binary.parent.mkdir()
    path_binary.write_text("#!/bin/sh\n", encoding="utf-8")
    path_binary.chmod(0o755)
    monkeypatch.setattr(shutil, "which", lambda name: str(path_binary))
    ytdlp_resolver._clear_cache()

    assert ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig()) == str(path_binary)


def test_verified_asset_installs(
    isolated_ytdlp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = b"verified-binary" * 80_000
    digest = hashlib.sha256(binary).hexdigest()
    manifest = f"{digest}  {_ASSET_NAME}\n".encode()
    _install_network(monkeypatch, binary, manifest=manifest)

    installed = YtdlpUpdater(AnkiMinerConfig())._download_and_install(_ASSET_URL, _TAG)

    assert installed == isolated_ytdlp_home / "bin" / "yt-dlp"
    assert installed.read_bytes() == binary
    assert os.access(installed, os.X_OK)
    assert installed.with_name("yt-dlp.verified").read_text(encoding="ascii").strip() == digest

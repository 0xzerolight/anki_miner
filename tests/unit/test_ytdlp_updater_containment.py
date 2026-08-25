"""Security containment tests for the app-managed yt-dlp updater (048).

Every response is an in-memory fake.  These tests must never contact GitHub.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QTimer

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.main_window import MainWindow
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.services import youtube_fetcher, ytdlp_invocation, ytdlp_updater
from anki_miner.services.validation_service import ValidationService
from anki_miner.services.youtube_fetcher import YouTubeFetcherService, YtdlpNotFoundError
from anki_miner.services.ytdlp_updater import YtdlpUpdater
from anki_miner.utils import ytdlp_resolver
from anki_miner.utils.process_supervisor import SupervisedResult, SupervisedState

_ORIGINAL_MAYBE_START_YTDLP_UPDATE = MainWindow._maybe_start_ytdlp_update
_TAG = "2026.07.20"
# Derived, not literal: these tests force sys.platform = "linux" and the asset
# name is a production detail. Hardcoding it made the asset URL and the manifest
# bodies below disagree with the code under test the moment the linux asset moved
# off the zipapp, which downgraded three of the parametrized cases into vacuous
# passes on a URL-refusal error instead of the property each one names.
_ASSET_NAME = ytdlp_updater._ASSET_BY_PLATFORM["linux"]
_ASSET_URL = f"https://github.com/yt-dlp/yt-dlp/releases/download/{_TAG}/{_ASSET_NAME}"
_SUMS_URL = f"https://github.com/yt-dlp/yt-dlp/releases/download/{_TAG}/SHA2-256SUMS"
# The host GitHub 302s release downloads to today. Previously this named the
# retired objects.githubusercontent.com, which is why the whole suite stayed green
# while every real download was refused.
_ALLOWED_FINAL_URL = "https://release-assets.githubusercontent.com/yt-dlp-release-asset"


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


_GOOD_ENTRY = b"0" * 64 + b"  " + _ASSET_NAME.encode() + b"\n"


@pytest.mark.parametrize(
    ("manifest", "expected_exc", "expected_match"),
    [
        pytest.param(None, OSError, "SHA2-256SUMS missing", id="missing_sums"),
        pytest.param(b"0" * 64 + b"  another-file\n", ValueError, "has no entry", id="missing_entry"),
        pytest.param(_GOOD_ENTRY * 2, ValueError, "duplicate entries", id="duplicate_entry"),
        pytest.param(_GOOD_ENTRY, ValueError, "does not match", id="wrong_hash"),
    ],
)
def test_unverified_ytdlp_asset_never_installs_or_executes(
    manifest: bytes | None,
    expected_exc: type[BaseException],
    expected_match: str,
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
    # Assert the specific failure each param names. A bare raises((OSError,
    # ValueError)) also swallows "Refusing non-release or mismatched ... URL", so a
    # drifted asset name would let every case pass without reaching the manifest
    # logic at all.
    with pytest.raises(expected_exc, match=expected_match):
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


def _run_maybe_start(config: AnkiMinerConfig, monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
    """Drive MainWindow._maybe_start_ytdlp_update against *config*, recording effects."""
    scheduled: list[tuple[int, object]] = []
    starts: list[object] = []
    window = SimpleNamespace(
        config=config,
        background_tasks=SimpleNamespace(start_ytdlp_update=lambda *args, **kwargs: starts.append((args, kwargs))),
    )
    monkeypatch.setattr(QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))
    _ORIGINAL_MAYBE_START_YTDLP_UPDATE(window)
    return scheduled, starts


def test_opted_out_startup_starts_no_downloader(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto_update_ytdlp=False must start nothing at startup.

    This is the containment property from 048, and it is unchanged. What changed is
    only which configs *have* False: the dataclass default is now True, so this test
    sets it explicitly rather than relying on the default.
    """
    config = replace(AnkiMinerConfig(), auto_update_ytdlp=False)
    scheduled, starts = _run_maybe_start(config, monkeypatch)

    assert scheduled == []
    assert starts == []


def test_fresh_default_startup_schedules_the_updater(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh install opts in, so the throttled background check is scheduled.

    Deliberate behavior change: keeping yt-dlp current is what keeps YouTube mining
    working, and a bundled binary is pinned at build time. It reaches only installs
    with no config file — every file the app has written carries an explicit value.
    The download itself remains host-allowlisted, SHA-256 verified, atomically
    installed, and receipt-gated before the resolver will select it.
    """
    config = AnkiMinerConfig()
    assert config.auto_update_ytdlp is True
    scheduled, starts = _run_maybe_start(config, monkeypatch)

    # Deferred via singleShot rather than run inline, so the window paints before any
    # network call. starts stays empty until the scheduled callback actually fires.
    assert len(scheduled) == 1
    assert starts == []

    delay, callback = scheduled[0]
    assert delay == 0
    callback()
    assert len(starts) == 1
    args, kwargs = starts[0]
    # force=False keeps the 24h throttle in charge of the startup check.
    assert kwargs == {"force": False}
    assert args == (config,)


def test_existing_config_migrated_to_updater_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", config_path)

    # Pinned to the literal pre-shim version, NOT CONFIG_SCHEMA_VERSION - 1:
    # the shim gates on "< 3", so a later bump would silently stop exercising it.
    config_path.write_text(
        json.dumps({"config_schema_version": 2, "auto_update_ytdlp": True}),
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


def test_resolver_requires_a_receipt_for_the_managed_binary(
    isolated_ytdlp_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sibling_ytdlp,
) -> None:
    """Verification is a precondition of selecting the managed slot.

    Renamed from ``..._and_prefers_path``: the managed tier now outranks PATH so a
    completed update is not inert (see ytdlp_resolver's module docstring). The
    containment property this test exists for is unchanged — a receiptless or
    tampered managed binary is never selected, not even when PATH points straight
    at it — and is asserted three times below.
    """
    managed = isolated_ytdlp_home / "bin" / "yt-dlp"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"managed")
    managed.chmod(0o755)
    # Even if the managed slot itself appears on PATH, PATH must not launder a
    # receiptless app download into a trusted executable.
    monkeypatch.setattr(shutil, "which", lambda name: str(managed))

    with pytest.raises(FileNotFoundError, match="unverified managed yt-dlp"):
        ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig())

    receipt = managed.with_name("yt-dlp.verified")
    receipt.write_text(hashlib.sha256(managed.read_bytes()).hexdigest(), encoding="ascii")
    ytdlp_resolver._clear_cache()
    assert ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig()) == str(managed)

    managed.write_bytes(b"tampered")
    with pytest.raises(FileNotFoundError, match="unverified managed yt-dlp"):
        ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig())

    receipt.write_text(hashlib.sha256(managed.read_bytes()).hexdigest(), encoding="ascii")
    path_binary = tmp_path / "path-bin" / "yt-dlp"
    path_binary.parent.mkdir()
    path_binary.write_text("#!/bin/sh\n", encoding="utf-8")
    path_binary.chmod(0o755)
    monkeypatch.setattr(shutil, "which", lambda name: str(path_binary))
    ytdlp_resolver._clear_cache()

    # A re-verified managed copy now wins over an unrelated PATH binary. This is
    # the assertion that flipped with the tier reorder; the three raises above are
    # the security property and must keep passing untouched.
    assert ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig()) == str(managed)

    # ...and with the managed copy gone, PATH is still honored.
    managed.unlink()
    receipt.unlink()
    ytdlp_resolver._clear_cache()
    assert ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig()) == str(path_binary)


def test_resolver_rejects_receiptless_managed_binary_on_real_path(
    isolated_ytdlp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = isolated_ytdlp_home / "bin" / "yt-dlp"
    managed.parent.mkdir(parents=True)
    managed.write_text("#!/bin/sh\n", encoding="utf-8")
    managed.chmod(0o755)
    monkeypatch.setenv("PATH", str(managed.parent))

    with pytest.raises(FileNotFoundError, match="unverified managed yt-dlp"):
        ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig())


def test_resolver_rejects_receiptless_managed_binary_through_hardlink(
    isolated_ytdlp_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = isolated_ytdlp_home / "bin" / "yt-dlp"
    managed.parent.mkdir(parents=True)
    managed.write_text("#!/bin/sh\n", encoding="utf-8")
    managed.chmod(0o755)
    path_alias = tmp_path / "path-bin" / "yt-dlp"
    path_alias.parent.mkdir()
    os.link(managed, path_alias)
    monkeypatch.setattr(shutil, "which", lambda name: str(path_alias))

    with pytest.raises(FileNotFoundError, match="unverified managed yt-dlp"):
        ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig())


def test_fetcher_translates_rejected_managed_path_before_subprocess(
    isolated_ytdlp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = isolated_ytdlp_home / "bin" / "yt-dlp"
    managed.parent.mkdir(parents=True)
    managed.write_text("#!/bin/sh\n", encoding="utf-8")
    managed.chmod(0o755)
    monkeypatch.setenv("PATH", str(managed.parent))

    def unexpected_run(*args, **kwargs):
        raise AssertionError("unverified managed binary reached subprocess")

    monkeypatch.setattr("anki_miner.services.ytdlp_invocation.subprocess.run", unexpected_run)

    with pytest.raises(YtdlpNotFoundError, match="Update yt-dlp now"):
        YouTubeFetcherService(AnkiMinerConfig()).probe_metadata("https://youtu.be/abc123")


def test_resolver_rechecks_cached_path_alias_into_managed_dir(
    isolated_ytdlp_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = tmp_path / "path-bin" / "yt-dlp"
    external.parent.mkdir()
    external.write_text("#!/bin/sh\n", encoding="utf-8")
    external.chmod(0o755)
    monkeypatch.setattr(shutil, "which", lambda name: str(external))
    assert ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig()) == str(external)

    managed = isolated_ytdlp_home / "bin" / "yt-dlp"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"verified-managed")
    managed.chmod(0o755)
    managed.with_name("yt-dlp.verified").write_text(
        hashlib.sha256(managed.read_bytes()).hexdigest(),
        encoding="ascii",
    )
    receiptless = managed.parent / "receiptless-yt-dlp"
    receiptless.write_bytes(b"receiptless")
    receiptless.chmod(0o755)
    external.unlink()
    external.symlink_to(receiptless)

    assert ytdlp_resolver.resolve_ytdlp(AnkiMinerConfig()) == str(managed)


def test_staged_file_mutated_after_stream_hash_is_not_promoted(
    isolated_ytdlp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = b"verified-binary" * 80_000
    digest = hashlib.sha256(binary).hexdigest()
    manifest = f"{digest}  {_ASSET_NAME}\n".encode()
    bin_dir = isolated_ytdlp_home / "bin"

    def fake_urlopen(request: object, timeout: int | None = None):  # noqa: ARG001
        url = _request_url(request)
        if url == _ASSET_URL:
            return _FakeResponse(binary, _ALLOWED_FINAL_URL)
        if url == _SUMS_URL:
            [staged] = bin_dir.glob("*.tmp")
            staged.write_bytes(b"tampered-after-stream-hash")
            return _FakeResponse(manifest, _ALLOWED_FINAL_URL)
        raise AssertionError(f"unexpected network request: {url}")

    monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="does not match"):
        YtdlpUpdater(AnkiMinerConfig())._download_and_install(_ASSET_URL, _TAG)

    final = bin_dir / "yt-dlp"
    assert not final.exists()
    assert not final.with_name("yt-dlp.verified").exists()


def test_oversized_sums_manifest_is_rejected(
    isolated_ytdlp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = b"verified-binary" * 80_000
    manifest = b"x" * (256 * 1024 + 1)
    manifest_read_sizes: list[int] = []

    class _TrackedManifestResponse(_FakeResponse):
        def read(self, size: int = -1) -> bytes:
            manifest_read_sizes.append(size)
            return super().read(size)

    def fake_urlopen(request: object, timeout: int | None = None):  # noqa: ARG001
        url = _request_url(request)
        if url == _ASSET_URL:
            return _FakeResponse(binary, _ALLOWED_FINAL_URL)
        if url == _SUMS_URL:
            return _TrackedManifestResponse(manifest, _ALLOWED_FINAL_URL)
        raise AssertionError(f"unexpected network request: {url}")

    monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="exceeds the 262,144-byte cap"):
        YtdlpUpdater(AnkiMinerConfig())._download_and_install(_ASSET_URL, _TAG)

    assert manifest_read_sizes == [256 * 1024 + 1]


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


def test_active_managed_process_defers_update_without_throttling(
    isolated_ytdlp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = b"verified-binary" * 80_000
    digest = hashlib.sha256(binary).hexdigest()
    manifest = f"{digest}  {_ASSET_NAME}\n".encode()
    _install_network(monkeypatch, binary, manifest=manifest)
    monkeypatch.setattr(YtdlpUpdater, "latest_version_and_asset", lambda self: (_TAG, _ASSET_URL))
    monkeypatch.setattr(YtdlpUpdater, "local_version", lambda self: "2026.01.01")
    updater = YtdlpUpdater(AnkiMinerConfig())
    results: list = []

    ytdlp_updater._INSTALL_LOCK.acquire()
    try:
        thread = threading.Thread(target=lambda: results.append(updater.check_and_update()))
        thread.start()
        thread.join(0.2)
        deferred_without_waiting = not thread.is_alive()
    finally:
        ytdlp_updater._INSTALL_LOCK.release()
    thread.join(2)

    assert deferred_without_waiting is True
    assert results[0].action == "deferred"
    assert not updater._throttle_path().exists()


def _write_verified_managed_binary(home: Path, body: bytes = b"old-managed") -> Path:
    managed = home / "bin" / "yt-dlp"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(body)
    managed.chmod(0o755)
    managed.with_name("yt-dlp.verified").write_text(
        hashlib.sha256(body).hexdigest(),
        encoding="ascii",
    )
    ytdlp_resolver._clear_cache()
    return managed


def test_validation_ytdlp_probe_ignores_user_config(
    isolated_ytdlp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = _write_verified_managed_binary(isolated_ytdlp_home)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "2026.07.20\n", "")

    monkeypatch.setattr("anki_miner.services.validation_service.subprocess.run", fake_run)

    ok, _message = ValidationService(AnkiMinerConfig())._check_ytdlp()

    assert ok is True
    assert commands == [[str(managed), "--ignore-config", "--version"]]


def test_validation_ytdlp_probe_defers_update_without_throttling(
    isolated_ytdlp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_verified_managed_binary(isolated_ytdlp_home)
    probe_started = threading.Event()
    release_probe = threading.Event()
    validation_result: list[tuple[bool, str]] = []

    def blocking_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        probe_started.set()
        assert release_probe.wait(2)
        return subprocess.CompletedProcess(command, 0, "2026.07.20\n", "")

    monkeypatch.setattr("anki_miner.services.validation_service.subprocess.run", blocking_run)
    monkeypatch.setattr(
        YtdlpUpdater,
        "latest_version_and_asset",
        lambda self: (_ for _ in ()).throw(AssertionError("update reached network while validation ran")),
    )
    validation_thread = threading.Thread(
        target=lambda: validation_result.append(ValidationService(AnkiMinerConfig())._check_ytdlp())
    )
    validation_thread.start()
    assert probe_started.wait(2)
    try:
        update_result = YtdlpUpdater(AnkiMinerConfig()).check_and_update()
    finally:
        release_probe.set()
        validation_thread.join(2)

    assert not validation_thread.is_alive()
    assert validation_result[0][0] is True
    assert update_result.action == "deferred"
    assert not YtdlpUpdater(AnkiMinerConfig())._throttle_path().exists()


def test_promotion_and_cache_invalidation_block_probe_command_construction(
    isolated_ytdlp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = _write_verified_managed_binary(isolated_ytdlp_home)
    binary = b"verified-binary" * 80_000
    digest = hashlib.sha256(binary).hexdigest()
    manifest = f"{digest}  {_ASSET_NAME}\n".encode()
    _install_network(monkeypatch, binary, manifest=manifest)
    monkeypatch.setattr(YtdlpUpdater, "latest_version_and_asset", lambda self: (_TAG, _ASSET_URL))
    monkeypatch.setattr(YtdlpUpdater, "local_version", lambda self: "2026.01.01")

    ytdlp_invocation.ytdlp_supports_js_runtimes.cache_clear()
    ytdlp_invocation.ytdlp_supports_remote_components.cache_clear()
    monkeypatch.setattr(
        ytdlp_invocation.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "old help", ""),
    )
    assert ytdlp_invocation.ytdlp_supports_js_runtimes(str(managed)) is False
    assert ytdlp_invocation.ytdlp_supports_remote_components(str(managed)) is False

    invalidation_started = threading.Event()
    release_invalidation = threading.Event()
    real_clear = ytdlp_resolver._clear_cache

    def blocking_clear() -> None:
        invalidation_started.set()
        assert release_invalidation.wait(2)
        real_clear()

    monkeypatch.setattr(ytdlp_resolver, "_clear_cache", blocking_clear)
    monkeypatch.setattr(
        ytdlp_invocation.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            "--js-runtimes --remote-components",
            "",
        ),
    )
    monkeypatch.setattr(
        youtube_fetcher.shutil,
        "which",
        lambda name: "/usr/bin/node" if name == "node" else None,
    )
    probe_commands: list[list[str]] = []
    probe_done = threading.Event()

    def fake_supervised(command: list[str], **kwargs: object) -> SupervisedResult:  # noqa: ARG001
        probe_commands.append(command)
        return SupervisedResult(
            SupervisedState.COMPLETED,
            0,
            json.dumps(
                {
                    "id": "dQw4w9WgXcQ",
                    "title": "Test Video",
                    "duration": 120,
                    "subtitles": {},
                    "automatic_captions": {},
                }
            ),
            "",
        )

    monkeypatch.setattr(youtube_fetcher, "run_supervised", fake_supervised)
    update_results: list = []
    probe_errors: list[BaseException] = []
    updater_thread = threading.Thread(
        target=lambda: update_results.append(YtdlpUpdater(AnkiMinerConfig()).check_and_update())
    )

    def run_probe() -> None:
        try:
            YouTubeFetcherService(AnkiMinerConfig()).probe_metadata("https://youtu.be/dQw4w9WgXcQ")
        except BaseException as exc:  # noqa: BLE001 - forwarded to the test thread
            probe_errors.append(exc)
        finally:
            probe_done.set()

    probe_thread = threading.Thread(target=run_probe)
    updater_thread.start()
    assert invalidation_started.wait(2)
    probe_thread.start()
    try:
        assert not probe_done.wait(0.2)
    finally:
        release_invalidation.set()
        updater_thread.join(3)
        probe_thread.join(3)
        ytdlp_invocation.ytdlp_supports_js_runtimes.cache_clear()
        ytdlp_invocation.ytdlp_supports_remote_components.cache_clear()

    assert not updater_thread.is_alive()
    assert not probe_thread.is_alive()
    assert probe_errors == []
    assert update_results[0].action == "installed"
    assert probe_commands[0][0] == str(managed)
    assert "--js-runtimes" in probe_commands[0]
    assert "--remote-components" in probe_commands[0]


def test_resolver_cache_writer_cannot_republish_stale_path_after_promotion(
    isolated_ytdlp_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = tmp_path / "path-bin" / "yt-dlp"
    external.parent.mkdir()
    external.write_text("#!/bin/sh\n", encoding="utf-8")
    external.chmod(0o755)
    monkeypatch.setattr(ytdlp_resolver.shutil, "which", lambda name: str(external))
    ytdlp_resolver._clear_cache()

    compute_finished = threading.Event()
    release_compute = threading.Event()
    real_compute = ytdlp_resolver._compute

    def blocking_compute(*args: object, **kwargs: object) -> str:
        resolved = real_compute(*args, **kwargs)
        compute_finished.set()
        assert release_compute.wait(2)
        return resolved

    monkeypatch.setattr(ytdlp_resolver, "_compute", blocking_compute)
    config = AnkiMinerConfig()
    resolutions: list[str] = []
    resolution_errors: list[BaseException] = []

    def resolve_in_thread() -> None:
        try:
            resolutions.append(ytdlp_resolver.resolve_ytdlp(config))
        except BaseException as exc:  # noqa: BLE001 - forwarded to the test thread
            resolution_errors.append(exc)

    resolver_thread = threading.Thread(target=resolve_in_thread)
    resolver_thread.start()
    assert compute_finished.wait(2)

    managed = isolated_ytdlp_home / "bin" / "yt-dlp"
    managed.parent.mkdir(parents=True, exist_ok=True)
    staged = managed.with_name("staged-yt-dlp")
    body = b"new-managed"
    staged.write_bytes(body)
    staged.chmod(0o755)
    digest = hashlib.sha256(body).hexdigest()
    updater = YtdlpUpdater(config)

    try:
        with ytdlp_resolver.managed_ytdlp_lock(blocking=False) as acquired:
            initial_promotion_acquired = acquired
            if acquired:
                updater._promote_verified_binary(staged, managed, digest)
                ytdlp_resolver._clear_cache()
    finally:
        release_compute.set()
        resolver_thread.join(2)

    assert not resolver_thread.is_alive()
    assert resolution_errors == []
    assert resolutions == [str(external)]

    if not initial_promotion_acquired:
        with ytdlp_resolver.managed_ytdlp_lock(blocking=False) as acquired:
            assert acquired
            updater._promote_verified_binary(staged, managed, digest)
            ytdlp_resolver._clear_cache()

    assert ytdlp_resolver.resolve_ytdlp(config) == str(managed)

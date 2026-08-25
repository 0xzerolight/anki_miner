"""Tests for the yt-dlp binary runtime resolver."""

import dataclasses
import hashlib
import shutil
import threading
import time
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.utils import ytdlp_resolver
from anki_miner.utils.ytdlp_resolver import (
    resolve_ytdlp,
    ytdlp_binary_name,
    ytdlp_download_dir,
    ytdlp_generation_lock,
)


@pytest.fixture
def base_config(tmp_path):
    return AnkiMinerConfig(media_temp_folder=tmp_path / "temp_media")


@pytest.fixture(autouse=True)
def no_path_ytdlp(monkeypatch, no_sibling_ytdlp):
    """Keep non-PATH resolver tiers deterministic on developer machines.

    Patching ``shutil.which`` alone is not sufficient: the interpreter-sibling tier
    never consults PATH, and ``.venv/bin/yt-dlp`` exists wherever the project is
    installed, so ``no_sibling_ytdlp`` (tests/conftest.py) is pulled in too.
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)


def _make_executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def _write_receipt(path):
    receipt = path.with_name(f"{path.name}.verified")
    receipt.write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    return receipt


class TestBinaryName:
    def test_linux_name(self, monkeypatch):
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        assert ytdlp_binary_name() == "yt-dlp"

    def test_macos_name(self, monkeypatch):
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "darwin")
        assert ytdlp_binary_name() == "yt-dlp"

    def test_windows_name(self, monkeypatch):
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "win32")
        assert ytdlp_binary_name() == "yt-dlp.exe"


class TestDownloadDir:
    def test_is_home_bin(self, monkeypatch, tmp_path):
        # ytdlp_download_dir reads ANKI_MINER_HOME at call time.
        monkeypatch.setattr(ytdlp_resolver.paths, "ANKI_MINER_HOME", tmp_path)
        assert ytdlp_download_dir() == tmp_path / "bin"


class TestResolveYtdlp:
    def test_default_returns_bare_literal(self, base_config, monkeypatch):
        # No override, not frozen, no downloaded copy -> bare "yt-dlp".
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        assert resolve_ytdlp(base_config) == "yt-dlp"

    def test_config_override_wins_when_file_exists(self, base_config, tmp_path):
        binary = _make_executable(tmp_path / "my-yt-dlp")
        config = dataclasses.replace(base_config, ytdlp_location=binary)
        assert resolve_ytdlp(config) == str(binary)

    def test_config_override_ignored_when_file_missing(self, base_config, tmp_path, monkeypatch):
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        config = dataclasses.replace(base_config, ytdlp_location=tmp_path / "does-not-exist")
        assert resolve_ytdlp(config) == "yt-dlp"

    def test_config_override_ignored_when_not_executable(self, base_config, tmp_path, monkeypatch):
        override = tmp_path / "override-yt-dlp"
        override.write_text("#!/bin/sh\n")
        override.chmod(0o644)
        fallback = _make_executable(tmp_path / "path-bin" / "yt-dlp")
        monkeypatch.setattr(shutil, "which", lambda name: str(fallback))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        config = dataclasses.replace(base_config, ytdlp_location=override)

        assert resolve_ytdlp(config) == str(fallback)

    def test_verified_downloaded_copy_used(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "home" / "bin"
        downloaded = _make_executable(bin_dir / "yt-dlp")
        _write_receipt(downloaded)
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: bin_dir)
        assert resolve_ytdlp(base_config) == str(downloaded)

    def test_downloaded_non_exec_falls_through(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "home" / "bin"
        bin_dir.mkdir(parents=True)
        downloaded = bin_dir / "yt-dlp"
        downloaded.write_text("#!/bin/sh\n")
        downloaded.chmod(0o644)
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: bin_dir)
        assert resolve_ytdlp(base_config) == "yt-dlp"

    def test_bundled_used_when_frozen(self, base_config, tmp_path, monkeypatch):
        bundled = _make_executable(tmp_path / "bin" / "yt-dlp")
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        # No downloaded copy in the (isolated) home.
        assert resolve_ytdlp(base_config) == str(bundled)

    def test_bundled_non_executable_falls_through(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "yt-dlp"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o644)
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        assert resolve_ytdlp(base_config) == "yt-dlp"

    def test_bundled_windows_exe_name(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "yt-dlp.exe"
        bundled.write_text("binary")
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "win32")
        assert resolve_ytdlp(base_config) == str(bundled)

    def test_downloaded_beats_bundled(self, base_config, tmp_path, monkeypatch):
        download_dir = tmp_path / "home" / "bin"
        downloaded = _make_executable(download_dir / "yt-dlp")
        _write_receipt(downloaded)
        bundled = _make_executable(tmp_path / "bin" / "yt-dlp")
        assert bundled.exists()
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        assert resolve_ytdlp(base_config) == str(downloaded)

    def test_override_beats_downloaded(self, base_config, tmp_path, monkeypatch):
        override = _make_executable(tmp_path / "override-yt-dlp")
        download_dir = tmp_path / "home" / "bin"
        _make_executable(download_dir / "yt-dlp")
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        config = dataclasses.replace(base_config, ytdlp_location=override)
        assert resolve_ytdlp(config) == str(override)


class TestTierPrecedence:
    """Pin the relative order of the managed / PATH / bundled tiers.

    None of this was covered before: every test in this module nulls
    ``shutil.which``, so PATH never competed with another tier.
    """

    def test_verified_managed_beats_path(self, base_config, tmp_path, monkeypatch):
        """A completed update must actually take effect.

        With PATH first, "Update yt-dlp now" installed into ~/.anki_miner/bin but
        the app kept running the stale PATH binary — and the next check compared
        against that stale version, re-downloading every 24h forever.
        """
        download_dir = tmp_path / "home" / "bin"
        downloaded = _make_executable(download_dir / "yt-dlp")
        _write_receipt(downloaded)
        path_binary = _make_executable(tmp_path / "path-bin" / "yt-dlp")
        monkeypatch.setattr(shutil, "which", lambda name: str(path_binary))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        assert resolve_ytdlp(base_config) == str(downloaded)

    def test_path_wins_when_no_managed_copy(self, base_config, tmp_path, monkeypatch):
        """Users who never used the updater keep their own binary."""
        download_dir = tmp_path / "home" / "bin"
        path_binary = _make_executable(tmp_path / "path-bin" / "yt-dlp")
        monkeypatch.setattr(shutil, "which", lambda name: str(path_binary))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        assert resolve_ytdlp(base_config) == str(path_binary)

    def test_path_beats_bundled_when_frozen(self, base_config, tmp_path, monkeypatch):
        """Deliberate divergence from ffmpeg_resolver/alass_resolver.

        yt-dlp breaks whenever YouTube changes something, so a user's own binary is
        usually fresher than a build-time pin. Bundled-first would silently
        downgrade the one population that never had the missing-binary bug. Do not
        "fix" this toward consistency with the sibling resolvers.
        """
        bundled = _make_executable(tmp_path / "bin" / "yt-dlp")
        path_binary = _make_executable(tmp_path / "path-bin" / "yt-dlp")
        monkeypatch.setattr(shutil, "which", lambda name: str(path_binary))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        assert bundled.exists()
        assert resolve_ytdlp(base_config) == str(path_binary)

    def test_managed_beats_bundled_when_frozen(self, base_config, tmp_path, monkeypatch):
        """The bundle smoke cannot cover this: it always runs with an empty home.

        ``scripts/bundle_smoke.sh`` points ANKI_MINER_HOME at a fresh mktemp dir, so
        the managed slot is guaranteed empty there and this precedence is never
        exercised by the release gate.
        """
        download_dir = tmp_path / "home" / "bin"
        downloaded = _make_executable(download_dir / "yt-dlp")
        _write_receipt(downloaded)
        bundled = _make_executable(tmp_path / "bin" / "yt-dlp")
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        assert bundled.exists()
        assert resolve_ytdlp(base_config) == str(downloaded)


class TestInterpreterSiblingTier:
    """The pip/pipx console script next to ``sys.executable``.

    ``pipx install anki_miner`` puts a working yt-dlp in the pipx venv's ``bin/``,
    which is not on PATH, so without this tier it is never found.
    """

    def test_sibling_used_when_nothing_else_resolves(self, base_config, tmp_path, monkeypatch):
        venv_bin = tmp_path / "venv" / "bin"
        sibling = _make_executable(venv_bin / "yt-dlp")
        monkeypatch.setattr(ytdlp_resolver.sys, "executable", str(venv_bin / "python"))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: tmp_path / "home" / "bin")
        ytdlp_resolver._clear_cache()
        assert resolve_ytdlp(base_config) == str(sibling)

    def test_sibling_skipped_when_frozen(self, base_config, tmp_path, monkeypatch):
        """In a frozen bundle ``sys.executable`` is the app, not an interpreter."""
        app_dir = tmp_path / "app"
        _make_executable(app_dir / "yt-dlp")
        monkeypatch.setattr(ytdlp_resolver.sys, "executable", str(app_dir / "AnkiMiner"))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: tmp_path / "home" / "bin")
        ytdlp_resolver._clear_cache()
        assert resolve_ytdlp(base_config) == "yt-dlp"

    def test_sibling_inside_managed_dir_is_refused(self, base_config, tmp_path, monkeypatch):
        """The sibling tier must not become a second laundering route.

        If ``sys.executable`` sits in (or symlinks into) the managed directory, a
        receiptless managed binary must still never be selected — the same property
        the PATH tier is guarded for.
        """
        download_dir = tmp_path / "home" / "bin"
        receiptless = _make_executable(download_dir / "yt-dlp")
        assert receiptless.exists()
        monkeypatch.setattr(ytdlp_resolver.sys, "executable", str(download_dir / "python"))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        ytdlp_resolver._clear_cache()
        assert resolve_ytdlp(base_config) == "yt-dlp"

    def test_rejected_managed_path_hit_still_raises_with_sibling_present(self, base_config, tmp_path, monkeypatch):
        """The fail-closed raise must stay ahead of the sibling tier.

        Otherwise a rejected receiptless managed binary falls through to a real
        executable and the containment the raise exists for is silently defeated.
        """
        download_dir = tmp_path / "home" / "bin"
        receiptless = _make_executable(download_dir / "yt-dlp")
        venv_bin = tmp_path / "venv" / "bin"
        _make_executable(venv_bin / "yt-dlp")
        monkeypatch.setattr(shutil, "which", lambda name: str(receiptless))
        monkeypatch.setattr(ytdlp_resolver.sys, "executable", str(venv_bin / "python"))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        ytdlp_resolver._clear_cache()
        with pytest.raises(FileNotFoundError, match="unverified managed yt-dlp"):
            resolve_ytdlp(base_config)


class TestYtdlpAvailable:
    """``ytdlp_available`` must absorb the fail-closed raise, unlike alass_available."""

    def test_true_for_a_verified_managed_copy(self, base_config, tmp_path, monkeypatch):
        download_dir = tmp_path / "home" / "bin"
        downloaded = _make_executable(download_dir / "yt-dlp")
        _write_receipt(downloaded)
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        assert ytdlp_resolver.ytdlp_available(base_config) is True

    def test_false_when_nothing_resolves(self, base_config, tmp_path, monkeypatch):
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: tmp_path / "home" / "bin")
        assert ytdlp_resolver.ytdlp_available(base_config) is False

    def test_false_instead_of_raising_on_rejected_managed_path_hit(self, base_config, tmp_path, monkeypatch):
        """The rejection must not escape as FileNotFoundError.

        ValidationService.validate_setup documents itself as never raising and has
        no blanket try around its checks, so an availability probe that propagates
        would take the whole startup validation down.
        """
        download_dir = tmp_path / "home" / "bin"
        receiptless = _make_executable(download_dir / "yt-dlp")
        monkeypatch.setattr(shutil, "which", lambda name: str(receiptless))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        ytdlp_resolver._clear_cache()
        with pytest.raises(FileNotFoundError):
            ytdlp_resolver.resolve_ytdlp(base_config)
        ytdlp_resolver._clear_cache()
        assert ytdlp_resolver.ytdlp_available(base_config) is False


class TestCaching:
    def test_cache_cleared_unmasks_fresh_download(self, base_config, tmp_path, monkeypatch):
        # First call: no download present -> bare literal.
        download_dir = tmp_path / "home" / "bin"
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        assert resolve_ytdlp(base_config) == "yt-dlp"

        # A download appears after startup; without _clear_cache the stale
        # literal would be returned. The updater calls _clear_cache() post-install.
        downloaded = _make_executable(download_dir / "yt-dlp")
        _write_receipt(downloaded)
        ytdlp_resolver._clear_cache()
        assert resolve_ytdlp(base_config) == str(downloaded)

    def test_cache_does_not_mask_changed_override(self, base_config, tmp_path):
        first = _make_executable(tmp_path / "yt-dlp-a")
        second = _make_executable(tmp_path / "yt-dlp-b")
        cfg_a = dataclasses.replace(base_config, ytdlp_location=first)
        cfg_b = dataclasses.replace(base_config, ytdlp_location=second)
        assert resolve_ytdlp(cfg_a) == str(first)
        assert resolve_ytdlp(cfg_b) == str(second)

    def test_deleted_cached_override_unmasks_path_fallback(self, base_config, tmp_path, monkeypatch):
        override = _make_executable(tmp_path / "override-yt-dlp")
        fallback = _make_executable(tmp_path / "path-bin" / "yt-dlp")
        monkeypatch.setattr(shutil, "which", lambda name: str(fallback))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        config = dataclasses.replace(base_config, ytdlp_location=override)
        assert resolve_ytdlp(config) == str(override)

        override.unlink()

        assert resolve_ytdlp(config) == str(fallback)

    def test_non_executable_cached_override_unmasks_path_fallback(self, base_config, tmp_path, monkeypatch):
        override = _make_executable(tmp_path / "override-yt-dlp")
        fallback = _make_executable(tmp_path / "path-bin" / "yt-dlp")
        monkeypatch.setattr(shutil, "which", lambda name: str(fallback))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        config = dataclasses.replace(base_config, ytdlp_location=override)
        assert resolve_ytdlp(config) == str(override)

        override.chmod(0o644)

        assert resolve_ytdlp(config) == str(fallback)

    def test_deleted_cached_relative_override_unmasks_path_fallback(self, base_config, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        override = _make_executable(Path("yt-dlp"))
        fallback = _make_executable(tmp_path / "path-bin" / "yt-dlp")
        monkeypatch.setattr(shutil, "which", lambda name: str(fallback))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        config = dataclasses.replace(base_config, ytdlp_location=override)
        assert resolve_ytdlp(config) == str(override)

        override.unlink()

        assert resolve_ytdlp(config) == str(fallback)

    def test_non_executable_cached_relative_override_unmasks_path_fallback(self, base_config, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        override = _make_executable(Path("yt-dlp"))
        fallback = _make_executable(tmp_path / "path-bin" / "yt-dlp")
        monkeypatch.setattr(shutil, "which", lambda name: str(fallback))
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        config = dataclasses.replace(base_config, ytdlp_location=override)
        assert resolve_ytdlp(config) == str(override)

        override.chmod(0o644)

        assert resolve_ytdlp(config) == str(fallback)


def _acquirable_from_another_thread() -> bool:
    """True when a foreign thread can take the generation lock right now.

    The lock is an RLock, so a non-blocking acquire on the holding thread always
    succeeds — the probe has to run somewhere else to mean anything.
    """
    seen: list[bool] = []

    def probe() -> None:
        with ytdlp_resolver.managed_ytdlp_lock(blocking=False) as acquired:
            seen.append(acquired)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()
    return seen == [True]


class TestManagedLockTimeout:
    """``timeout`` bounds a blocking acquire for callers that can neither park nor give up instantly."""

    def _acquired_from_another_thread(self, *, timeout: float) -> bool:
        seen: list[bool] = []

        def probe() -> None:
            with ytdlp_resolver.managed_ytdlp_lock(timeout=timeout) as acquired:
                seen.append(acquired)

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(10)
        assert not thread.is_alive()
        return seen == [True]

    def test_expires_to_false_while_a_holder_keeps_the_lock(self):
        with ytdlp_resolver.managed_ytdlp_lock():
            assert not self._acquired_from_another_thread(timeout=0.2)

    def test_acquires_once_a_transient_holder_releases(self):
        holding = threading.Event()

        def hold() -> None:
            with ytdlp_resolver.managed_ytdlp_lock():
                holding.set()
                time.sleep(0.2)

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            assert holding.wait(10)
            assert self._acquired_from_another_thread(timeout=10.0)
        finally:
            holder.join(10)
        assert not holder.is_alive()

    def test_rejects_a_timeout_on_a_non_blocking_acquire(self):
        """Meaningless combination — raise rather than silently ignore one of the two."""
        with pytest.raises(ValueError), ytdlp_resolver.managed_ytdlp_lock(blocking=False, timeout=1.0):
            pass

    def test_a_foreign_executable_still_passes_through(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ytdlp_resolver.paths, "ANKI_MINER_HOME", tmp_path / "home")
        with (
            ytdlp_resolver.managed_ytdlp_lock(),
            ytdlp_resolver.managed_ytdlp_lock("/usr/bin/yt-dlp", timeout=0.2) as acquired,
        ):
            assert acquired is True


class TestGenerationLock:
    """``ytdlp_generation_lock`` covers argv construction always, exec only for the managed slot."""

    @pytest.fixture(autouse=True)
    def _isolated_home(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp_resolver.paths, "ANKI_MINER_HOME", tmp_path / "home")

    def test_lock_is_held_while_the_command_is_built(self):
        with ytdlp_generation_lock():
            assert not _acquirable_from_another_thread()
        assert _acquirable_from_another_thread()

    def test_non_managed_executable_releases_before_execution(self):
        with ytdlp_generation_lock() as release_unless_managed:
            assert not _acquirable_from_another_thread()
            release_unless_managed("/usr/bin/yt-dlp")
            assert _acquirable_from_another_thread()

    def test_managed_executable_keeps_the_lock_through_execution(self, tmp_path):
        managed = tmp_path / "home" / "bin" / ytdlp_binary_name()
        with ytdlp_generation_lock() as release_unless_managed:
            release_unless_managed(managed)
            assert not _acquirable_from_another_thread()
        assert _acquirable_from_another_thread()

    def test_a_sibling_of_the_managed_slot_also_keeps_the_lock(self, tmp_path):
        """Anything inside the managed directory shares its Windows image lock."""
        staged = tmp_path / "home" / "bin" / "yt-dlp.new"
        with ytdlp_generation_lock() as release_unless_managed:
            release_unless_managed(staged)
            assert not _acquirable_from_another_thread()

    def test_repeated_release_does_not_over_release(self):
        with ytdlp_generation_lock() as release_unless_managed:
            release_unless_managed("/usr/bin/yt-dlp")
            release_unless_managed("/usr/bin/yt-dlp")
        assert _acquirable_from_another_thread()

    def test_release_inside_an_outer_hold_leaves_the_outer_hold_intact(self):
        """Re-entrant use only drops this frame's count, never the caller's."""
        with ytdlp_resolver.managed_ytdlp_lock():
            with ytdlp_generation_lock() as release_unless_managed:
                release_unless_managed("/usr/bin/yt-dlp")
                assert not _acquirable_from_another_thread()
            assert not _acquirable_from_another_thread()
        assert _acquirable_from_another_thread()

    def test_exception_inside_the_block_releases(self):
        with pytest.raises(RuntimeError), ytdlp_generation_lock():
            raise RuntimeError("boom")
        assert _acquirable_from_another_thread()

"""In-place update staging (services/app_updater.py): AppImage and Windows only."""

from __future__ import annotations

import hashlib
import os
import stat
import sys

import pytest

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.services import app_updater
from anki_miner.services.update_checker import UpdateInfo

NEW_BYTES = b"new anki miner build " * 1000
NEW_SHA = hashlib.sha256(NEW_BYTES).hexdigest()
ASSET_URL = "https://github.com/0xzerolight/anki_miner/releases/download/v9.9.9/asset"


def _info(target: str, *, sha: str | None = NEW_SHA, url: str | None = ASSET_URL) -> UpdateInfo:
    return UpdateInfo(
        version="9.9.9",
        release_page_url="",
        asset_url=url,
        release_notes="",
        asset_sha256=sha,
        target=target,
    )


@pytest.fixture
def fake_download(monkeypatch):
    """Stand in for the network: write NEW_BYTES to a .part in dest_dir."""
    calls: list[dict] = []

    def _download(url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None):
        calls.append({"url": url, "dest_dir": dest_dir, "max_bytes": max_bytes})
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = dest_dir / "tmpdownload.part"
        part.write_bytes(NEW_BYTES)
        if progress is not None:
            progress(len(NEW_BYTES), len(NEW_BYTES), "Downloading")
        return part

    monkeypatch.setattr(app_updater, "download_to_temp", _download)
    return calls


@pytest.fixture
def appimage(tmp_path, monkeypatch):
    path = tmp_path / "apps" / "AnkiMiner-3.6.0-Linux-x86_64.AppImage"
    path.parent.mkdir()
    path.write_bytes(b"old build")
    path.chmod(0o755)
    monkeypatch.setenv("APPIMAGE", str(path))
    return path


@pytest.fixture
def inno_install(tmp_path, monkeypatch):
    """A Windows install tree with Inno's uninstaller, and a private stage dir."""
    install = tmp_path / "install"
    install.mkdir()
    (install / "AnkiMiner.exe").write_bytes(b"")
    (install / "unins000.exe").write_bytes(b"")
    monkeypatch.setattr(app_updater.sys, "executable", str(install / "AnkiMiner.exe"))
    stage = tmp_path / "stage"
    monkeypatch.setattr(app_updater, "_windows_stage_dir", lambda: stage)
    return stage


class TestInPlaceSupported:
    def test_a_writable_appimage(self, appimage):
        assert app_updater.in_place_supported(_info("appimage")) is True

    @pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions; root ignores them")
    def test_a_read_only_appimage_folder(self, appimage):
        appimage.parent.chmod(0o555)
        try:
            assert app_updater.in_place_supported(_info("appimage")) is False
        finally:
            appimage.parent.chmod(0o755)

    def test_no_digest_means_no_in_place(self, appimage):
        assert app_updater.in_place_supported(_info("appimage", sha=None)) is False

    def test_no_asset_means_no_in_place(self, appimage):
        assert app_updater.in_place_supported(_info("appimage", url=None)) is False

    def test_appimage_target_without_the_env_var(self, monkeypatch):
        monkeypatch.delenv("APPIMAGE", raising=False)
        assert app_updater.in_place_supported(_info("appimage")) is False

    def test_an_inno_install(self, inno_install):
        assert app_updater.in_place_supported(_info("windows-frozen")) is True

    def test_a_windows_tree_without_the_uninstaller(self, tmp_path, monkeypatch):
        (tmp_path / "AnkiMiner.exe").write_bytes(b"")
        monkeypatch.setattr(app_updater.sys, "executable", str(tmp_path / "AnkiMiner.exe"))
        assert app_updater.in_place_supported(_info("windows-frozen")) is False

    @pytest.mark.parametrize("target", ["linux-frozen", "macos-frozen-arm64", "macos-frozen-x86_64", "pip"])
    def test_every_other_install_keeps_the_browser_download(self, target):
        assert app_updater.in_place_supported(_info(target)) is False


class TestStageAppImage:
    def test_replaces_the_running_appimage_in_place(self, appimage, fake_download):
        staged = app_updater.stage_update(_info("appimage"))

        assert staged == app_updater.StagedUpdate(version="9.9.9", target="appimage", path=appimage)
        assert appimage.read_bytes() == NEW_BYTES
        assert appimage.stat().st_mode & stat.S_IXUSR
        # Same folder as the AppImage: the final os.replace must not cross filesystems.
        assert fake_download[0]["dest_dir"] == appimage.parent
        assert fake_download[0]["max_bytes"] == app_updater.MAX_UPDATE_BYTES

    def test_checksum_mismatch_keeps_the_old_build_and_no_part(self, appimage, fake_download):
        with pytest.raises(SetupError):
            app_updater.stage_update(_info("appimage", sha="0" * 64))

        assert appimage.read_bytes() == b"old build"
        assert list(appimage.parent.glob("*.part")) == []

    def test_cancel_keeps_the_old_build(self, appimage, monkeypatch):
        def _cancelled(*_args, **_kwargs):
            raise OperationCancelled("Download cancelled")

        monkeypatch.setattr(app_updater, "download_to_temp", _cancelled)
        with pytest.raises(OperationCancelled):
            app_updater.stage_update(_info("appimage"))

        assert appimage.read_bytes() == b"old build"

    def test_refuses_an_install_that_cannot_update_itself(self, appimage, fake_download):
        with pytest.raises(SetupError):
            app_updater.stage_update(_info("appimage", sha=None))

        assert fake_download == []


class TestStageWindows:
    def test_stages_a_verified_setup(self, inno_install, fake_download):
        staged = app_updater.stage_update(_info("windows-frozen"))

        assert staged.path == inno_install / "AnkiMiner-9.9.9-Setup.exe"
        assert staged.path.read_bytes() == NEW_BYTES
        assert list(inno_install.glob("*.part")) == []

    def test_a_previously_staged_setup_is_removed_first(self, inno_install, fake_download):
        inno_install.mkdir()
        stale = inno_install / "AnkiMiner-9.9.8-Setup.exe"
        stale.write_bytes(b"old")

        app_updater.stage_update(_info("windows-frozen"))

        assert not stale.exists()

    def test_relaunch_runs_setup_in_update_mode(self, inno_install, fake_download):
        staged = app_updater.stage_update(_info("windows-frozen"))

        assert app_updater.relaunch_command(staged) == (
            staged.path,
            ("/SILENT", "/SP-", "/NOCANCEL", "/NORESTART", "/UPDATE=1"),
        )


def test_appimage_relaunch_is_the_ordinary_restart(tmp_path):
    staged = app_updater.StagedUpdate(version="9.9.9", target="appimage", path=tmp_path / "x.AppImage")
    assert app_updater.relaunch_command(staged) is None

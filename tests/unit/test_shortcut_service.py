"""Tests for ShortcutService."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.shortcut_service import (
    APP_ID,
    APP_NAME,
    ShortcutResult,
    ShortcutService,
)


class TestShortcutResult:
    def test_default_result_is_failure(self):
        result = ShortcutResult()
        assert result.success is False
        assert result.messages == []
        assert result.paths_created == []
        assert result.error is None

    def test_success_result_with_paths(self, tmp_path):
        path = tmp_path / "shortcut"
        result = ShortcutResult(success=True, paths_created=[path], messages=["ok"])
        assert result.success is True
        assert result.paths_created == [path]
        assert result.messages == ["ok"]


class TestShortcutExists:
    def test_returns_false_when_no_shortcut(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("sys.platform", "linux"):
            assert ShortcutService.shortcut_exists() is False

    def test_returns_true_when_linux_desktop_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        desktop_dir = tmp_path / ".local" / "share" / "applications"
        desktop_dir.mkdir(parents=True)
        (desktop_dir / f"{APP_ID}.desktop").write_text("[Desktop Entry]")

        with patch("sys.platform", "linux"):
            assert ShortcutService.shortcut_exists() is True

    def test_returns_true_when_windows_lnk_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        (desktop / f"{APP_NAME}.lnk").write_text("dummy")

        with patch("sys.platform", "win32"):
            assert ShortcutService.shortcut_exists() is True

    def test_returns_false_on_unsupported_platform(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("sys.platform", "freebsd"):
            assert ShortcutService.shortcut_exists() is False


class TestFindExecutable:
    def test_uses_sys_executable_when_frozen(self, tmp_path):
        fake_exe = tmp_path / "anki_miner_gui"
        fake_exe.touch()

        with (
            patch.object(sys, "executable", str(fake_exe)),
            patch.object(sys, "frozen", True, create=True),
        ):
            result = ShortcutService._find_executable()
        assert result == fake_exe.resolve()

    def test_finds_via_path_when_not_frozen(self, tmp_path):
        fake_exe = tmp_path / "anki_miner_gui"
        fake_exe.touch()

        if hasattr(sys, "frozen"):
            with (
                patch.object(sys, "frozen", False),
                patch("shutil.which", return_value=str(fake_exe)),
            ):
                result = ShortcutService._find_executable()
        else:
            with patch("shutil.which", return_value=str(fake_exe)):
                result = ShortcutService._find_executable()

        assert result == fake_exe.resolve()

    def test_returns_none_when_executable_missing(self, tmp_path):
        with patch("shutil.which", return_value=None), patch.object(sys, "prefix", str(tmp_path)):
            # Ensure not frozen
            if hasattr(sys, "frozen"):
                with patch.object(sys, "frozen", False):
                    result = ShortcutService._find_executable()
            else:
                result = ShortcutService._find_executable()
        assert result is None


class TestCreateShortcut:
    def test_returns_failure_when_executable_not_found(self):
        with patch.object(ShortcutService, "_find_executable", return_value=None):
            result = ShortcutService.create_shortcut()
        assert result.success is False
        assert result.error is not None
        assert "anki_miner_gui" in result.error

    def test_creates_linux_desktop_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        fake_exe = tmp_path / "anki_miner_gui"
        fake_exe.touch()

        with (
            patch.object(ShortcutService, "_find_executable", return_value=fake_exe),
            patch("sys.platform", "linux"),
            patch("subprocess.run"),
        ):  # avoid update-desktop-database call
            result = ShortcutService.create_shortcut()

        desktop_file = tmp_path / ".local" / "share" / "applications" / f"{APP_ID}.desktop"
        assert result.success is True
        assert desktop_file.exists()
        content = desktop_file.read_text()
        assert f"Name={APP_NAME}" in content
        assert f"Exec={fake_exe}" in content
        assert desktop_file in result.paths_created

    def test_macos_returns_unsupported_message(self, tmp_path):
        fake_exe = tmp_path / "anki_miner_gui"
        fake_exe.touch()
        with (
            patch.object(ShortcutService, "_find_executable", return_value=fake_exe),
            patch("sys.platform", "darwin"),
        ):
            result = ShortcutService.create_shortcut()
        # macOS path is informational (no shortcut created) but should not error
        assert result.success is True
        assert any("macOS" in m for m in result.messages)

    def test_windows_invokes_powershell(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / "Desktop").mkdir()
        fake_exe = tmp_path / "anki_miner_gui.exe"
        fake_exe.touch()

        completed = MagicMock(returncode=0, stderr="")
        with (
            patch.object(ShortcutService, "_find_executable", return_value=fake_exe),
            patch("sys.platform", "win32"),
            patch("subprocess.run", return_value=completed) as mock_run,
        ):
            result = ShortcutService.create_shortcut()

        assert result.success is True
        assert mock_run.called
        first_call_args = mock_run.call_args_list[0].args[0]
        assert first_call_args[0] == "powershell"


@pytest.fixture(autouse=True)
def _isolate_subprocess():
    """Prevent any test from accidentally invoking real subprocesses."""
    yield

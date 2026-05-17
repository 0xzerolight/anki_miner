"""Desktop shortcut creation service.

Cross-platform shortcut creation for Anki Miner GUI. Supports Linux (.desktop
file), Windows (.lnk), and macOS (informational only). Replaces the previous
CLI-driven `create-shortcut` command with a pure service the GUI can call.
"""

import contextlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "Anki Miner"
APP_ID = "anki-miner"
APP_COMMENT = "Japanese vocabulary mining from anime subtitles"
ICON_FILENAME = "anki_miner.svg"


@dataclass
class ShortcutResult:
    """Structured outcome of a shortcut creation attempt."""

    success: bool = False
    messages: list[str] = field(default_factory=list)
    paths_created: list[Path] = field(default_factory=list)
    error: str | None = None


def _get_icon_source() -> Path:
    """Resolve icon source, honoring PyInstaller frozen bundles."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "anki_miner" / "gui" / "resources" / "icons"
    return Path(__file__).resolve().parent.parent / "gui" / "resources" / "icons"


class ShortcutService:
    """Create and detect desktop shortcuts for the GUI app."""

    @staticmethod
    def shortcut_exists() -> bool:
        """Check whether a shortcut already exists for the current platform."""
        if sys.platform == "linux":
            return (Path.home() / ".local" / "share" / "applications" / f"{APP_ID}.desktop").exists()
        if sys.platform == "win32":
            return (Path.home() / "Desktop" / f"{APP_NAME}.lnk").exists()
        return False

    @staticmethod
    def _find_executable() -> Path | None:
        """Locate the anki_miner_gui executable (or frozen binary)."""
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve()

        exe = shutil.which("anki_miner_gui")
        if exe:
            return Path(exe).resolve()

        venv_dir = Path(sys.prefix)
        if sys.platform == "win32":
            candidate = venv_dir / "Scripts" / "anki_miner_gui.exe"
        else:
            candidate = venv_dir / "bin" / "anki_miner_gui"

        if candidate.exists():
            return candidate.resolve()
        return None

    @classmethod
    def create_shortcut(cls) -> ShortcutResult:
        """Create a desktop shortcut on the current platform."""
        result = ShortcutResult()

        exe_path = cls._find_executable()
        if exe_path is None:
            result.error = (
                "Could not find 'anki_miner_gui' executable. "
                "Make sure Anki Miner is installed (pip install .) and try again."
            )
            return result

        result.messages.append(f"Found executable: {exe_path}")

        if sys.platform == "linux":
            cls._create_linux_shortcut(exe_path, result)
        elif sys.platform == "win32":
            cls._create_windows_shortcut(exe_path, result)
        elif sys.platform == "darwin":
            result.success = True
            result.messages.append(
                f"Automatic shortcut creation is not supported on macOS. " f"To launch {APP_NAME}, run:\n  {exe_path}"
            )
        else:
            result.error = f"Unsupported platform: {sys.platform}"

        return result

    @staticmethod
    def _create_linux_shortcut(exe_path: Path, result: ShortcutResult) -> None:
        icon_dest_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
        icon_dest_dir.mkdir(parents=True, exist_ok=True)

        icon_source = _get_icon_source() / ICON_FILENAME
        icon_dest = icon_dest_dir / f"{APP_ID}.svg"

        if icon_source.exists():
            shutil.copy2(icon_source, icon_dest)
            result.messages.append(f"Icon installed: {icon_dest}")
            result.paths_created.append(icon_dest)
        else:
            result.messages.append(f"Warning: icon not found at {icon_source}; using default icon.")

        desktop_dir = Path.home() / ".local" / "share" / "applications"
        desktop_dir.mkdir(parents=True, exist_ok=True)

        desktop_file = desktop_dir / f"{APP_ID}.desktop"
        desktop_content = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment={APP_COMMENT}
Exec={exe_path}
Icon={APP_ID}
Categories=Education;Languages;
Terminal=false
StartupWMClass=anki_miner
"""
        desktop_file.write_text(desktop_content)
        desktop_file.chmod(0o755)
        result.messages.append(f"Desktop file created: {desktop_file}")
        result.paths_created.append(desktop_file)

        with contextlib.suppress(FileNotFoundError):
            subprocess.run(
                ["update-desktop-database", str(desktop_dir)],
                capture_output=True,
                check=False,
            )

        result.success = True
        result.messages.append(f"'{APP_NAME}' should now appear in your application menu.")

    @staticmethod
    def _create_windows_shortcut(exe_path: Path, result: ShortcutResult) -> None:
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            desktop = Path.home()
            result.messages.append(f"Desktop folder not found, using {desktop}")

        shortcut_path = desktop / f"{APP_NAME}.lnk"

        ps_script = (
            "$ws = New-Object -ComObject WScript.Shell; "
            f'$s = $ws.CreateShortcut("{shortcut_path}"); '
            f'$s.TargetPath = "{exe_path}"; '
            f'$s.WorkingDirectory = "{exe_path.parent}"; '
            f'$s.IconLocation = "{exe_path}, 0"; '
            f'$s.Description = "{APP_COMMENT}"; '
            "$s.Save()"
        )

        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                check=True,
                capture_output=True,
                text=True,
            )
            result.messages.append(f"Desktop shortcut created: {shortcut_path}")
            result.paths_created.append(shortcut_path)
        except subprocess.CalledProcessError as exc:
            result.error = f"Error creating shortcut: {exc.stderr}"
            return
        except FileNotFoundError:
            result.error = "PowerShell not found. Cannot create shortcut."
            return

        start_menu = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if start_menu.exists():
            start_shortcut = start_menu / f"{APP_NAME}.lnk"
            ps_script_start = (
                "$ws = New-Object -ComObject WScript.Shell; "
                f'$s = $ws.CreateShortcut("{start_shortcut}"); '
                f'$s.TargetPath = "{exe_path}"; '
                f'$s.WorkingDirectory = "{exe_path.parent}"; '
                f'$s.IconLocation = "{exe_path}, 0"; '
                f'$s.Description = "{APP_COMMENT}"; '
                "$s.Save()"
            )
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_script_start],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                result.messages.append(f"Start Menu shortcut created: {start_shortcut}")
                result.paths_created.append(start_shortcut)
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # optional

        result.success = True

"""CLI command for creating a desktop shortcut for Anki Miner GUI.

Supports Linux (.desktop file) and Windows (.lnk shortcut).
"""

import contextlib
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Anki Miner"
APP_ID = "anki-miner"
APP_COMMENT = "Japanese vocabulary mining from anime subtitles"
ICON_FILENAME = "anki_miner.svg"


def _get_icon_source() -> Path:
    """Get the icon source directory, accounting for PyInstaller bundles."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "anki_miner" / "gui" / "resources" / "icons"
    return Path(__file__).resolve().parent.parent.parent / "gui" / "resources" / "icons"


# Resolve the icon path relative to the installed package
ICON_SOURCE = _get_icon_source()


def _find_executable() -> Path | None:
    """Find the anki_miner_gui executable."""
    # Check PATH first (works for pip install, pipx, system installs)
    exe = shutil.which("anki_miner_gui")
    if exe:
        return Path(exe).resolve()

    # Check current venv's bin/Scripts directory
    venv_dir = Path(sys.prefix)
    if sys.platform == "win32":
        candidates = [venv_dir / "Scripts" / "anki_miner_gui.exe"]
    else:
        candidates = [venv_dir / "bin" / "anki_miner_gui"]

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    return None


def _create_linux_shortcut(exe_path: Path) -> None:
    """Create a .desktop file and install the icon on Linux."""
    # Install icon to the hicolor theme
    icon_dest_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    icon_dest_dir.mkdir(parents=True, exist_ok=True)

    icon_source = ICON_SOURCE / ICON_FILENAME
    icon_dest = icon_dest_dir / f"{APP_ID}.svg"

    if icon_source.exists():
        shutil.copy2(icon_source, icon_dest)
        print(f"Icon installed: {icon_dest}")
    else:
        print(f"Warning: Icon not found at {icon_source}, shortcut will use default icon")

    # Create .desktop file
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
    print(f"Desktop file created: {desktop_file}")

    # Update desktop database if available
    with contextlib.suppress(FileNotFoundError):
        subprocess.run(
            ["update-desktop-database", str(desktop_dir)],
            capture_output=True,
            check=False,
        )

    print(f"\n'{APP_NAME}' should now appear in your application menu.")
    print("You can also pin it to your taskbar/dock.")


def _create_windows_shortcut(exe_path: Path) -> None:
    """Create a .lnk shortcut on the Windows Desktop."""
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        # Some Windows locales use a translated Desktop folder name
        # Fall back to the user's home directory
        desktop = Path.home()
        print(f"Desktop folder not found, creating shortcut in: {desktop}")

    shortcut_path = desktop / f"{APP_NAME}.lnk"

    # Use PowerShell to create the .lnk shortcut (no extra dependencies needed)
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
        print(f"Desktop shortcut created: {shortcut_path}")
    except subprocess.CalledProcessError as e:
        print(f"Error creating shortcut: {e.stderr}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: PowerShell not found. Cannot create shortcut.")
        sys.exit(1)

    # Also create a Start Menu shortcut
    start_menu = (
        Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    )
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
            print(f"Start Menu shortcut created: {start_shortcut}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass  # Start Menu shortcut is optional

    print(f"\nYou can now launch '{APP_NAME}' from your Desktop.")


def create_shortcut_command(args) -> int:
    """Create a desktop shortcut for Anki Miner."""
    print(f"Creating desktop shortcut for {APP_NAME}...\n")

    exe_path = _find_executable()
    if exe_path is None:
        print(
            "Error: Could not find 'anki_miner_gui' executable.\n"
            "Make sure Anki Miner is installed: pip install .\n"
            "Then try again."
        )
        return 1

    print(f"Found executable: {exe_path}")

    if sys.platform == "linux":
        _create_linux_shortcut(exe_path)
    elif sys.platform == "win32":
        _create_windows_shortcut(exe_path)
    elif sys.platform == "darwin":
        print("Automatic shortcut creation is not yet supported on macOS.")
        print(f"\nTo launch {APP_NAME}, run this command in your terminal:")
        print(f"  {exe_path}")
        print("\nTo create a quick alias, add this to your ~/.zshrc or ~/.bash_profile:")
        print(f'  alias anki-miner="{exe_path}"')
    else:
        print(f"Unsupported platform: {sys.platform}")
        print("Supported platforms: Linux, Windows, macOS")

    return 0

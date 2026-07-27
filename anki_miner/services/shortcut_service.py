"""Desktop shortcut creation service.

Cross-platform shortcut creation for Anki Miner GUI. Supports Linux (.desktop
file), Windows (.lnk), and macOS (informational only). Replaces the previous
CLI-driven `create-shortcut` command with a pure service the GUI can call.
"""

import contextlib
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from anki_miner.utils.subprocess_utils import no_window_kwargs

logger = logging.getLogger(__name__)

APP_NAME = "Anki Miner"
APP_ID = "anki-miner"
APP_COMMENT = "Japanese vocabulary mining from media"
ICON_FILENAME = "anki_miner.svg"

# These helpers run synchronously on the GUI thread; bound them so a hung
# PowerShell / update-desktop-database can't freeze the whole app.
_SUBPROCESS_TIMEOUT_SECONDS = 10


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


def _format_desktop_exec(exe_path: Path) -> str:
    """Quote and escape *exe_path* for a freedesktop ``Exec=`` value.

    Per the Desktop Entry spec, a value containing reserved characters (notably
    spaces) must be double-quoted, with backslash escaping for the literal
    ``"``, `` ` ``, ``$`` and ``\\`` inside the quotes. A literal ``%`` is a
    field-code introducer and must be doubled to ``%%``.
    """
    escaped = str(exe_path).replace("\\", "\\\\")
    for ch in ('"', "`", "$"):
        escaped = escaped.replace(ch, "\\" + ch)
    escaped = escaped.replace("%", "%%")
    return f'"{escaped}"'


class ShortcutService:
    """Create and detect desktop shortcuts for the GUI app."""

    @staticmethod
    def shortcut_exists() -> bool:
        """Check whether a shortcut already exists for the current platform."""
        if sys.platform == "linux":
            return (Path.home() / ".local" / "share" / "applications" / f"{APP_ID}.desktop").exists()
        if sys.platform == "win32":
            ps_script = ShortcutService._windows_shortcut_path_script() + (
                "Write-Output (Test-Path -LiteralPath $shortcutPath)"
            )
            try:
                completed = ShortcutService._run_powershell(ps_script)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
                logger.warning("Windows shortcut existence check failed: %s", exc)
                return False
            exists = completed.stdout.strip().casefold()
            if exists not in {"true", "false"}:
                logger.warning("Windows shortcut existence check returned unexpected output: %r", completed.stdout)
                return False
            return exists == "true"
        return False

    @staticmethod
    def resolve_executable() -> Path | None:
        """Locate the anki_miner_gui executable (or frozen binary).

        Public because two features need the same answer: the desktop shortcut
        writes it into a launcher, and the restart-to-apply flow (decision D39b)
        relaunches it. Both must agree, and both must fail closed rather than
        guess — a shortcut pointing at a vanished AppImage mount and a restart
        that never comes back are the same bug.
        """
        # AppImage runtime sets APPIMAGE to the real .appimage path before Python
        # starts. sys.executable inside an AppImage is the ephemeral /tmp/.mount_*
        # FUSE path that vanishes when the app closes, so the APPIMAGE check MUST
        # come before the sys.frozen branch — otherwise the desktop entry's Exec
        # points at a mount that no longer exists on the next launch. Mirrors
        # update_checker._detect_target().
        appimage = os.environ.get("APPIMAGE")
        if appimage:
            return Path(appimage).resolve()
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
    def create_shortcut(
        cls,
        *,
        skip_if_exists: bool = False,
        include_start_menu: bool = True,
    ) -> ShortcutResult:
        """Create a desktop shortcut on the current platform."""
        result = ShortcutResult()

        exe_path = cls.resolve_executable()
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
            cls._create_windows_shortcut(
                exe_path,
                result,
                skip_if_exists=skip_if_exists,
                include_start_menu=include_start_menu,
            )
        elif sys.platform == "darwin":
            result.success = True
            result.messages.append(
                f"Automatic shortcut creation is not supported on macOS. To launch {APP_NAME}, run:\n  {exe_path}"
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
Exec={_format_desktop_exec(exe_path)}
Icon={APP_ID}
Categories=Education;Languages;
Terminal=false
StartupWMClass=anki_miner
"""
        desktop_file.write_text(desktop_content)
        desktop_file.chmod(0o755)
        result.messages.append(f"Desktop file created: {desktop_file}")
        result.paths_created.append(desktop_file)

        with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
            subprocess.run(
                ["update-desktop-database", str(desktop_dir)],
                capture_output=True,
                check=False,
                timeout=_SUBPROCESS_TIMEOUT_SECONDS,
                **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
            )

        result.success = True
        result.messages.append(f"'{APP_NAME}' should now appear in your application menu.")

    @staticmethod
    def _ps_quote(value: str) -> str:
        """Return *value* as a single-quoted PowerShell string literal.

        Single-quoted PS literals don't expand ``$`` or backtick (both legal in
        Windows paths, e.g. ``C:\\Users\\j$on``); an embedded single quote is
        escaped by doubling it.
        """
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _run_powershell(ps_script: str) -> subprocess.CompletedProcess[str]:
        """Run one bounded, hidden PowerShell command."""
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
        )

    @classmethod
    def _windows_shortcut_path_script(cls) -> str:
        """Build PowerShell that resolves the real Windows Desktop shortcut path."""
        resolution_error = QCoreApplication.translate("MainWindow", "Failed to create desktop shortcut.")
        return (
            "$ErrorActionPreference = 'Stop'; "
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$ws = New-Object -ComObject WScript.Shell; "
            "$desktop = $ws.SpecialFolders.Item('Desktop'); "
            "if ([string]::IsNullOrWhiteSpace($desktop)) { "
            f"throw {cls._ps_quote(resolution_error)} "
            "}; "
            f"$shortcutPath = Join-Path -Path $desktop -ChildPath {cls._ps_quote(f'{APP_NAME}.lnk')}; "
        )

    @classmethod
    def _create_windows_shortcut(
        cls,
        exe_path: Path,
        result: ShortcutResult,
        *,
        skip_if_exists: bool,
        include_start_menu: bool,
    ) -> None:
        ps_script = cls._windows_shortcut_path_script()
        if skip_if_exists:
            ps_script += (
                "if (Test-Path -LiteralPath $shortcutPath) { "
                "Write-Output ('EXISTS' + [char]9 + $shortcutPath); "
                "exit 0 "
                "}; "
            )

        ps_script += (
            "$s = $ws.CreateShortcut($shortcutPath); "
            f"$s.TargetPath = {cls._ps_quote(str(exe_path))}; "
            f"$s.WorkingDirectory = {cls._ps_quote(str(exe_path.parent))}; "
            f"$s.IconLocation = {cls._ps_quote(f'{exe_path}, 0')}; "
            f"$s.Description = {cls._ps_quote(APP_COMMENT)}; "
            "$s.Save(); "
            "[Console]::Out.WriteLine(('CREATED' + [char]9 + $shortcutPath)); "
            "[Console]::Out.Flush(); "
        )
        if include_start_menu:
            ps_script += (
                "try { "
                "$programs = $ws.SpecialFolders.Item('Programs'); "
                "if (-not [string]::IsNullOrWhiteSpace($programs)) { "
                f"$startShortcutPath = Join-Path -Path $programs -ChildPath {cls._ps_quote(f'{APP_NAME}.lnk')}; "
                "$s = $ws.CreateShortcut($startShortcutPath); "
                f"$s.TargetPath = {cls._ps_quote(str(exe_path))}; "
                f"$s.WorkingDirectory = {cls._ps_quote(str(exe_path.parent))}; "
                f"$s.IconLocation = {cls._ps_quote(f'{exe_path}, 0')}; "
                f"$s.Description = {cls._ps_quote(APP_COMMENT)}; "
                "$s.Save(); "
                "[Console]::Out.WriteLine(('CREATED' + [char]9 + $startShortcutPath)); "
                "[Console]::Out.Flush() "
                "} "
                "} catch { }"
            )

        try:
            completed = cls._run_powershell(ps_script)
        except subprocess.CalledProcessError as exc:
            result.error = f"Error creating shortcut: {exc.stderr}"
            logger.warning("Windows shortcut creation failed: %s", exc.stderr or exc)
            return
        except subprocess.TimeoutExpired as exc:
            partial_output = exc.stdout or ""
            if isinstance(partial_output, bytes):
                partial_output = partial_output.decode("utf-8", errors="replace")
            if cls._record_windows_shortcut_output(partial_output, result):
                result.success = True
                logger.warning("Windows shortcut creation timed out after creating a shortcut")
                return
            result.error = "PowerShell timed out while creating the shortcut."
            logger.warning("Windows shortcut creation failed: PowerShell timed out")
            return
        except FileNotFoundError:
            result.error = "PowerShell not found. Cannot create shortcut."
            logger.warning("Windows shortcut creation failed: PowerShell not found")
            return

        if not cls._record_windows_shortcut_output(completed.stdout, result):
            result.error = QCoreApplication.translate("MainWindow", "Failed to create desktop shortcut.")
            logger.warning("Windows shortcut creation failed: PowerShell returned no shortcut path")
            return

        result.success = True

    @staticmethod
    def _record_windows_shortcut_output(output: str, result: ShortcutResult) -> bool:
        """Record resolved shortcut paths emitted by the PowerShell command."""
        saw_shortcut = False
        for line in output.splitlines():
            status, separator, raw_path = line.partition("\t")
            if not separator or not raw_path or status not in {"CREATED", "EXISTS"}:
                continue
            saw_shortcut = True
            if status == "EXISTS":
                continue
            shortcut_path = Path(raw_path)
            if result.paths_created:
                result.messages.append(f"Start Menu shortcut created: {shortcut_path}")
            else:
                result.messages.append(f"Desktop shortcut created: {shortcut_path}")
            result.paths_created.append(shortcut_path)
        return saw_shortcut

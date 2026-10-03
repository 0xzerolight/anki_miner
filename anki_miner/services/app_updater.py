"""Install a newer Anki Miner over the running one: AppImage and Windows only.

The update banner offers this only where :func:`in_place_supported` says so;
macOS, the .deb and pip installs keep the browser download. Qt-free, like
``ytdlp_updater``: the GUI runs :func:`stage_update` on a worker thread.

Integrity is the sha256 the GitHub release API reports for the asset
(``UpdateInfo.asset_sha256``), checked before anything is replaced. That
authenticates the release over TLS, not a publisher key: the project ships
unsigned builds by owner decision, so there is no signature to check.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from anki_miner.exceptions import SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.services._install_common import cleanup_part, verify_sha256
from anki_miner.services.resource_downloader import CancelledCheck, download_to_temp
from anki_miner.services.update_checker import UpdateInfo
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

#: The largest release asset (the .deb) is about 300 MB; this only stops a
#: runaway response. Also keeps byte counts under 2**31 for the worker's
#: ``pyqtSignal(int, int)``.
MAX_UPDATE_BYTES = 1024 * 1024 * 1024

#: Inno Setup arguments for an update the app started.
#: /SILENT shows only the progress window. Message boxes are deliberately not
#: suppressed: once the app has exited they are the only way an install failure
#: reaches the user. /NOCANCEL because [InstallDelete] wipes _internal before
#: the new files land, so a cancel midway would leave an install that neither
#: runs nor relaunches. /UPDATE=1 makes Setup wait for the app to exit and
#: relaunch it afterwards (packaging/innosetup/anki_miner.iss, IsUpdateMode).
WINDOWS_SETUP_ARGUMENTS: tuple[str, ...] = ("/SILENT", "/SP-", "/NOCANCEL", "/NORESTART", "/UPDATE=1")


@dataclass(frozen=True, slots=True)
class StagedUpdate:
    """A verified update, ready for the restart that applies it.

    ``path`` is the replaced ``$APPIMAGE`` (already the new version) for
    ``target == "appimage"``, or the Setup.exe still to run for
    ``"windows-frozen"``.
    """

    version: str
    target: str
    path: Path


def _appimage_path() -> Path | None:
    value = os.environ.get("APPIMAGE")
    return Path(value) if value else None


def _windows_stage_dir() -> Path:
    """Where the Windows installer waits to run. In TEMP, beside Inno's own
    extraction, so a staged installer the user never ran is Windows' to clean."""
    return Path(tempfile.gettempdir()) / "anki-miner-update"


def in_place_supported(info: UpdateInfo) -> bool:
    """True when this install can apply *info* itself."""
    if info.asset_url is None or info.asset_sha256 is None:
        return False
    if info.target == "appimage":
        appimage = _appimage_path()
        # os.replace needs write access to the folder, not to the file.
        return appimage is not None and os.access(appimage.parent, os.W_OK)
    if info.target == "windows-frozen":
        # Only an Inno install has its uninstaller beside the exe. A copied or
        # dev tree would otherwise be "updated" by installing a second copy in
        # %LOCALAPPDATA%\Programs while the one the user runs stays old.
        return (Path(sys.executable).parent / "unins000.exe").is_file()
    return False


def stage_update(
    info: UpdateInfo,
    *,
    progress: DownloadProgressFn | None = None,
    cancelled_check: CancelledCheck | None = None,
) -> StagedUpdate:
    """Download, verify and stage *info*'s asset.

    AppImage: the verified file replaces ``$APPIMAGE`` atomically. The running
    process keeps its mount of the old file, and the next launch is the new
    version, whether the user restarts now or quits later.
    Windows: the verified installer is left in :func:`_windows_stage_dir`
    for :func:`relaunch_command`.

    Raises:
        SetupError: the install cannot update itself, the download failed, or
            the checksum did not match. The running build is untouched.
        OperationCancelled: *cancelled_check* fired. The running build is untouched.
    """
    if not in_place_supported(info) or info.asset_url is None or info.asset_sha256 is None:
        raise SetupError("This Anki Miner install cannot update itself.")

    if info.target == "appimage":
        appimage = _appimage_path()
        if appimage is None:
            raise SetupError("This Anki Miner install cannot update itself.")
        dest_dir = appimage.parent
        destination = appimage
    else:
        dest_dir = _windows_stage_dir()
        # One staged installer at a time: a leftover from an update the user
        # never restarted into is replaced, not accumulated.
        shutil.rmtree(dest_dir, ignore_errors=True)
        destination = dest_dir / f"AnkiMiner-{info.version}-Setup.exe"

    part = download_to_temp(
        info.asset_url,
        dest_dir=dest_dir,
        progress=progress,
        cancelled_check=cancelled_check,
        max_bytes=MAX_UPDATE_BYTES,
    )
    try:
        verify_sha256(part, info.asset_sha256, "Anki Miner update")
        if info.target == "appimage":
            os.chmod(part, 0o755)
        os.replace(part, destination)
    except BaseException:
        cleanup_part(part)
        raise
    log_summary(logger, "App update staged", version=info.version, target=info.target, path=destination)
    return StagedUpdate(version=info.version, target=info.target, path=destination)


def relaunch_command(staged: StagedUpdate) -> tuple[Path, tuple[str, ...]] | None:
    """What ``gui.app`` must start once the app has exited.

    ``None`` means the ordinary relaunch of this executable, which for an
    AppImage is the already-replaced ``$APPIMAGE``.
    """
    if staged.target == "windows-frozen":
        return staged.path, WINDOWS_SETUP_ARGUMENTS
    return None

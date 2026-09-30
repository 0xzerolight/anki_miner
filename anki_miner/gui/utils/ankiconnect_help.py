"""AnkiConnect install help and Anki's launcher, shared by the setup wizard and System Health.

The add-on code and the install steps used to live only on the wizard's Connect
page, so a user who pressed Skip Setup had nowhere to find them again (B09).
System Health now shows the same steps on its AnkiConnect row.

``anki_launch_command`` finds Anki where its own installers put it (D11). It
looks in exactly one standard place per platform and never searches: when it
finds nothing, the wizard keeps "Open Anki." as plain text.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QProcess

from anki_miner.utils.i18n import tr_format

__all__ = [
    "ANKICONNECT_ADDON_CODE",
    "anki_launch_command",
    "ankiconnect_install_help",
    "ankiconnect_install_steps",
    "launch_anki",
]

#: AnkiConnect's add-on code on AnkiWeb.
ANKICONNECT_ADDON_CODE = "2055492159"

#: Where the macOS installer puts Anki.
_MAC_BUNDLE = Path("/Applications/Anki.app")


def ankiconnect_install_steps() -> tuple[str, str, str]:
    """The three steps that get AnkiConnect running, translated, in order."""
    return (
        QCoreApplication.translate("AnkiConnectHelp", "Open Anki."),
        tr_format(
            QCoreApplication.translate(
                "AnkiConnectHelp",
                "In Anki choose Tools → Add-ons → Get Add-ons…, paste the code %1, and click OK.",
            ),
            ANKICONNECT_ADDON_CODE,
        ),
        QCoreApplication.translate("AnkiConnectHelp", "Restart Anki."),
    )


def ankiconnect_install_help() -> str:
    """The same three steps as one numbered block of plain text (System Health's row detail)."""
    return "\n".join(f"{number}. {step}" for number, step in enumerate(ankiconnect_install_steps(), start=1))


def anki_launch_command(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    mac_bundle: Path | None = None,
) -> list[str] | None:
    """The command that starts Anki from its standard install place, or None.

    Windows: ``%LOCALAPPDATA%\\Programs\\Anki\\anki.exe``. macOS:
    ``/Applications/Anki.app``, started through ``open``. Linux and anything
    else: ``anki`` on PATH. Flatpak and other launchers are not looked for.
    The keyword arguments exist for tests; callers pass none of them.
    """
    system = sys.platform if platform is None else platform
    env = os.environ if environ is None else environ
    if system == "win32":
        local = env.get("LOCALAPPDATA", "")
        if not local:
            return None
        exe = Path(local) / "Programs" / "Anki" / "anki.exe"
        return [str(exe)] if exe.is_file() else None
    if system == "darwin":
        bundle = _MAC_BUNDLE if mac_bundle is None else mac_bundle
        return ["open", str(bundle)] if bundle.is_dir() else None
    found = shutil.which("anki")
    return [found] if found else None


def launch_anki(command: list[str]) -> bool:
    """Start Anki detached from this process; True when the OS accepted the start."""
    started, _pid = QProcess.startDetached(command[0], command[1:])
    return bool(started)

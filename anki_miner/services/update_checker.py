"""Service for checking application updates from GitHub."""

import fnmatch
import json
import logging
import os
import sys
import urllib.request
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UpdateInfo:
    """Information about an available update.

    Attributes:
        version: Latest version string (e.g. "2.4.0", with the leading 'v' stripped).
        release_page_url: GitHub release HTML page URL — used as a fallback when
            no platform-matching asset is found.
        asset_url: Direct download URL for the asset matching the user's install
            method, or ``None`` if no match (e.g. pip/source installs).
        release_notes: Raw markdown body of the release (may be empty string).
    """

    version: str
    release_page_url: str
    asset_url: str | None
    release_notes: str


def _detect_target() -> str:
    """Detect the current install target.

    Returns one of: ``"appimage"``, ``"windows-frozen"``, ``"macos-frozen"``,
    ``"linux-frozen"``, ``"pip"``.
    """
    # AppImage runtime sets the APPIMAGE env var before Python starts. sys.frozen
    # is also True on AppImage (PyInstaller-built), so the APPIMAGE check MUST
    # come before any sys.frozen branches — otherwise AppImage users get matched
    # as plain linux-frozen and pointed at the .deb/.tar.gz instead.
    if os.environ.get("APPIMAGE"):
        return "appimage"
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            return "windows-frozen"
        if sys.platform == "darwin":
            return "macos-frozen"
        return "linux-frozen"
    return "pip"


# Asset name patterns for each target. linux-frozen has two patterns: prefer
# .deb (which integrates with the system package manager); fall back to the
# portable tar.gz when no .deb is published.
_TARGET_PATTERNS: dict[str, tuple[str, ...]] = {
    "windows-frozen": ("*-Windows-x86_64-Setup.exe",),
    "linux-frozen": ("anki-miner_*_amd64.deb", "AnkiMiner-Linux-x86_64.tar.gz"),
    "appimage": ("*-x86_64.AppImage",),
    "macos-frozen": ("AnkiMiner-macOS-arm64.tar.gz",),
}


def _pick_asset(assets: list[dict], target: str) -> str | None:
    """Pick the download URL for the asset matching the given target.

    Args:
        assets: GitHub API ``assets`` array — each entry is a dict with at
            least ``name`` and ``browser_download_url`` fields.
        target: Target string from :func:`_detect_target`.

    Returns:
        Direct download URL of the matched asset, or ``None`` if no asset
        matches (e.g. ``target == "pip"``).
    """
    patterns = _TARGET_PATTERNS.get(target)
    if not patterns:
        return None
    for pattern in patterns:
        for asset in assets:
            name = asset.get("name", "")
            if name and fnmatch.fnmatch(name, pattern):
                url = asset.get("browser_download_url")
                if isinstance(url, str) and url:
                    return url
    return None


class UpdateChecker:
    """Checks for new releases on GitHub.

    Compares the current version against the latest GitHub release tag
    to determine if an update is available.
    """

    GITHUB_API_URL = "https://api.github.com/repos/0xzerolight/anki_miner/releases/latest"

    def __init__(self, current_version: str):
        """Initialize the update checker.

        Args:
            current_version: Current application version string (e.g. "2.0.4")
        """
        self.current_version = current_version

    def check_for_update(self) -> UpdateInfo | None:
        """Check GitHub for the latest release.

        Returns:
            :class:`UpdateInfo` with ``asset_url`` populated for the user's
            install method when an update is available; ``None`` if the check
            fails (network error, invalid response, etc.) or if the installed
            version is already up to date.
        """
        try:
            request = urllib.request.Request(
                self.GITHUB_API_URL,
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    # GitHub requires a User-Agent header for abuse triage; omitting
                    # it occasionally yields 403 from anonymous unauthenticated calls.
                    "User-Agent": (
                        f"anki-miner/{self.current_version} "
                        "(+https://github.com/0xzerolight/anki_miner)"
                    ),
                },
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))

            tag_name = data.get("tag_name", "")
            release_page_url = data.get("html_url", "")
            release_notes = data.get("body") or ""
            assets = data.get("assets") or []

            # Strip leading 'v' if present (e.g. "v2.1.0" -> "2.1.0")
            latest_version = tag_name.lstrip("v")

            if not self._is_newer(latest_version, self.current_version):
                return None

            target = _detect_target()
            asset_url = _pick_asset(assets if isinstance(assets, list) else [], target)

            return UpdateInfo(
                version=latest_version,
                release_page_url=release_page_url,
                asset_url=asset_url,
                release_notes=release_notes,
            )

        except Exception:
            logger.debug("Failed to check for updates", exc_info=True)
            return None

    @staticmethod
    def _is_newer(latest: str, current: str) -> bool:
        """Compare two version strings using PEP 440 semantics.

        Uses :class:`packaging.version.Version` so prerelease (``2.3.5-rc1``)
        and post-release (``2.3.5.post1``) tags compare correctly.

        Args:
            latest: Latest version string (e.g. "2.1.0")
            current: Current version string (e.g. "2.0.4")

        Returns:
            True if ``latest`` is strictly newer than ``current``. Returns
            False if either string is empty or unparseable.
        """
        try:
            return Version(latest) > Version(current)
        except (InvalidVersion, TypeError):
            return False

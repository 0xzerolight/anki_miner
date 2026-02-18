"""Service for checking application updates from GitHub."""

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)


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

    def check_for_update(self) -> tuple[bool, str, str] | None:
        """Check GitHub for the latest release.

        Returns:
            Tuple of (update_available, latest_version, release_url) if the
            check succeeds, or None if the check fails (network error, etc.).
        """
        try:
            request = urllib.request.Request(
                self.GITHUB_API_URL,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))

            tag_name = data.get("tag_name", "")
            release_url = data.get("html_url", "")

            # Strip leading 'v' if present (e.g. "v2.1.0" -> "2.1.0")
            latest_version = tag_name.lstrip("v")

            update_available = self._is_newer(latest_version, self.current_version)
            return (update_available, latest_version, release_url)

        except Exception:
            logger.debug("Failed to check for updates", exc_info=True)
            return None

    @staticmethod
    def _is_newer(latest: str, current: str) -> bool:
        """Compare two version strings.

        Args:
            latest: Latest version string (e.g. "2.1.0")
            current: Current version string (e.g. "2.0.4")

        Returns:
            True if latest is newer than current.
        """
        try:
            latest_parts = tuple(int(x) for x in latest.split("."))
            current_parts = tuple(int(x) for x in current.split("."))
            return latest_parts > current_parts
        except (ValueError, AttributeError):
            return False

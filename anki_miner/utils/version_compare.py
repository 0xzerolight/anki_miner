"""Shared PEP 440 version-compare helper.

Factored out so the application updater (:mod:`anki_miner.services.update_checker`)
and the yt-dlp updater (:mod:`anki_miner.services.ytdlp_updater`) compare versions
with identical semantics.
"""

from packaging.version import InvalidVersion, Version

__all__ = ["is_newer"]


def is_newer(candidate: str, current: str) -> bool:
    """Return True iff *candidate* is strictly newer than *current* (PEP 440).

    Uses :class:`packaging.version.Version` so prerelease (``2.4.0-rc1``),
    post-release (``2.3.5.post1``), and date-based (``2024.03.10``) tags compare
    correctly. A naive ``tuple(int(x) for x in s.split("."))`` breaks on these.

    ``packaging`` is an EXPLICIT runtime dependency in pyproject.toml — do not
    assume setuptools (and its transitive ``packaging``) is present at runtime.

    Args:
        candidate: The version under consideration (e.g. a latest release).
        current: The version to compare against (e.g. the installed one).

    Returns:
        True if ``candidate`` is strictly newer than ``current``. False when
        either string is empty or unparseable (no spurious "newer").
    """
    try:
        return Version(candidate) > Version(current)
    except (InvalidVersion, TypeError):
        return False

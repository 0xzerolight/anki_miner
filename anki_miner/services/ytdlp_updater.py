"""Service that auto-downloads and self-updates the yt-dlp binary.

Mirrors :mod:`anki_miner.services.update_checker`: ``urllib.request`` with a
timeout + User-Agent header, a GitHub URL allowlist, ``packaging``-based version
comparison, and a "returns a result / never raises" contract.

The binary is installed into ``~/.anki_miner/bin/`` (see
:mod:`anki_miner.utils.ytdlp_resolver`, which prefers that copy). The YouTube
fetcher then runs that managed binary instead of relying on a PATH install.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from anki_miner.config import AnkiMinerConfig, paths
from anki_miner.utils import ytdlp_resolver
from anki_miner.utils.subprocess_utils import no_window_kwargs
from anki_miner.utils.version_compare import is_newer

logger = logging.getLogger(__name__)

# Latest-release endpoint for the yt-dlp project (no auth / key — free API).
GITHUB_API_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"

# Per-OS release asset name. yt-dlp ships a standalone binary per platform.
_ASSET_BY_PLATFORM: dict[str, str] = {
    "linux": "yt-dlp",
    "win32": "yt-dlp.exe",
    "darwin": "yt-dlp_macos",
}

# Allowlist for URLs we contact / download from. Only HTTPS on these hosts is
# accepted; everything else is fail-closed. (Mirrors update_checker's allowlist —
# copied rather than imported to keep the modules decoupled.)
_GITHUB_URL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "api.github.com",
    }
)

# 24h throttle window for the startup background check.
_THROTTLE_SECONDS = 24 * 60 * 60
# Reject a download whose final size is below this floor (partial / garbage).
_MIN_SIZE_BYTES = 1024 * 1024  # ~1 MB
# Streaming download chunk size.
_CHUNK_BYTES = 64 * 1024


def _validate_github_url(url: str) -> bool:
    """Return True iff *url* is an https URL on the GitHub allowlist (fail-closed)."""
    if not isinstance(url, str) or not url:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc.lower() in _GITHUB_URL_ALLOWLIST


@dataclass
class YtdlpUpdateResult:
    """Outcome of a :meth:`YtdlpUpdater.check_and_update` run.

    Attributes:
        action: One of ``"installed"``, ``"up_to_date"``, ``"skipped_throttle"``,
            ``"unavailable"``, ``"failed"``.
        installed_version: The version now on disk (post-install or current).
        available_version: The latest version reported by GitHub, if known.
        path: Path to the installed binary on ``"installed"``, else None.
        message: Human-readable summary for status surfaces.
    """

    action: str
    installed_version: str | None = None
    available_version: str | None = None
    path: Path | None = None
    message: str = ""


class YtdlpUpdater:
    """Download / self-update the app-managed yt-dlp binary. Never raises."""

    def __init__(self, config: AnkiMinerConfig, *, cancel: Callable[[], bool] | None = None) -> None:
        """Initialize the updater.

        Args:
            config: Live config (used to resolve the current yt-dlp for
                ``local_version`` and to honor a ``ytdlp_location`` override).
            cancel: Optional zero-arg predicate; when it returns True mid-download
                the install is aborted and cleaned up.
        """
        self._config = config
        self._cancel = cancel

    # --- paths -------------------------------------------------------------

    def download_dir(self) -> Path:
        """The app-managed yt-dlp directory (``~/.anki_miner/bin``)."""
        return ytdlp_resolver.ytdlp_download_dir()

    def _binary_name(self) -> str:
        return ytdlp_resolver.ytdlp_binary_name()

    def _throttle_path(self) -> Path:
        # Read ANKI_MINER_HOME at call time so test home-isolation applies.
        return paths.ANKI_MINER_HOME / ".ytdlp_update_check"

    # --- version probing ---------------------------------------------------

    def local_version(self) -> str | None:
        """Return the installed yt-dlp version, or None if absent/unparseable.

        Runs ``<yt-dlp> --version``. FileNotFoundError / timeout / any error
        yields None. Never raises.
        """
        cmd = [ytdlp_resolver.resolve_ytdlp(self._config), "--version"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                **no_window_kwargs(),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0:
            return None
        line = (proc.stdout or "").strip().splitlines()
        if not line:
            return None
        version = line[0].strip()
        return version or None

    def latest_version_and_asset(self) -> tuple[str | None, str | None]:
        """Return ``(latest_version, asset_download_url)`` from GitHub releases.

        Picks the per-OS asset and validates its URL against the allowlist. Any
        failure (network, parse, off-host) yields ``(None, None)`` (or a parsed
        version with a None URL if only the asset URL is off-host). Never raises.
        """
        try:
            request = urllib.request.Request(
                GITHUB_API_URL,
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "anki-miner (+https://github.com/0xzerolight/anki_miner)",
                },
            )
            if not _validate_github_url(GITHUB_API_URL):
                return (None, None)
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))

            tag_name = data.get("tag_name", "")
            version = tag_name.lstrip("v") or None
            if version is None:
                return (None, None)

            asset_name = _ASSET_BY_PLATFORM.get(sys.platform)
            url: str | None = None
            if asset_name:
                for asset in data.get("assets") or []:
                    if asset.get("name") == asset_name:
                        candidate = asset.get("browser_download_url")
                        if isinstance(candidate, str) and _validate_github_url(candidate):
                            url = candidate
                        break
            return (version, url)
        except Exception:
            logger.debug("yt-dlp latest-release lookup failed", exc_info=True)
            return (None, None)

    # --- throttle ----------------------------------------------------------

    def _throttled(self) -> bool:
        """True if the throttle file's mtime is within the throttle window."""
        path = self._throttle_path()
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return False
        return (time.time() - mtime) < _THROTTLE_SECONDS

    def _touch_throttle(self) -> None:
        """Write the current epoch to the throttle file atomically; suppress OSError."""
        path = self._throttle_path()
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(str(time.time()))
            os.replace(tmp, path)
        except OSError:
            logger.debug("Failed to write yt-dlp update throttle", exc_info=True)
            with contextlib.suppress(OSError):
                tmp.unlink()

    # --- orchestration -----------------------------------------------------

    def check_and_update(
        self,
        *,
        force: bool = False,
        cancel: Callable[[], bool] | None = None,
    ) -> YtdlpUpdateResult:
        """Throttled check + (if newer) download/install. Returns a result; never raises."""
        if cancel is not None:
            self._cancel = cancel
        try:
            if not force and self._throttled():
                return YtdlpUpdateResult(action="skipped_throttle", message="Checked recently; skipped.")

            # Write the throttle BEFORE the network call so a crash / tight loop
            # does not retry-storm GitHub.
            self._touch_throttle()

            latest, url = self.latest_version_and_asset()
            if latest is None:
                return YtdlpUpdateResult(action="unavailable", message="Could not reach GitHub releases.")

            local = self.local_version()
            if local and not is_newer(latest, local):
                return YtdlpUpdateResult(
                    action="up_to_date",
                    installed_version=local,
                    available_version=latest,
                    message=f"yt-dlp is up to date ({local}).",
                )

            if url is None:
                return YtdlpUpdateResult(
                    action="unavailable",
                    available_version=latest,
                    message="No downloadable asset for this platform.",
                )

            path = self._download_and_install(url, latest)
            ytdlp_resolver._clear_cache()
            return YtdlpUpdateResult(
                action="installed",
                installed_version=latest,
                available_version=latest,
                path=path,
                message=f"Updated yt-dlp to {latest}.",
            )
        except Exception as e:  # noqa: BLE001 — never propagate to the caller
            logger.exception("yt-dlp update failed")
            return YtdlpUpdateResult(action="failed", message=f"yt-dlp update failed: {e}")

    def _download_and_install(self, url: str, version: str) -> Path:
        """Stream-download *url* to a tmp file, validate, and atomically install it.

        Honors ``self._cancel`` between chunks. Cleans up the tmp on any failure.
        Returns the installed binary path. Raises on failure (the caller's
        ``check_and_update`` wraps it into a ``failed`` result).
        """
        if not _validate_github_url(url):
            raise ValueError(f"Refusing to download off-allowlist URL: {url!r}")

        bin_dir = self.download_dir()
        bin_dir.mkdir(parents=True, exist_ok=True)
        name = self._binary_name()
        final = bin_dir / name
        tmp = bin_dir / f"{name}.{os.getpid()}.tmp"

        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "anki-miner (+https://github.com/0xzerolight/anki_miner)"},
            )
            written = 0
            with urllib.request.urlopen(request, timeout=30) as response, open(tmp, "wb") as out:
                while True:
                    if self._cancel is not None and self._cancel():
                        raise RuntimeError("yt-dlp download cancelled")
                    chunk = response.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)

            if written < _MIN_SIZE_BYTES:
                raise ValueError(f"Downloaded yt-dlp is implausibly small ({written} bytes); rejecting.")

            if sys.platform != "win32":
                os.chmod(tmp, 0o755)
                if sys.platform == "darwin":
                    # Best-effort: strip the quarantine xattr so Gatekeeper does
                    # not block the freshly-downloaded binary. Failure is fine.
                    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                        subprocess.run(
                            ["xattr", "-d", "com.apple.quarantine", str(tmp)],
                            capture_output=True,
                            timeout=10,
                            **no_window_kwargs(),
                        )

            self._atomic_replace(tmp, final)
            logger.info("Installed yt-dlp %s to %s", version, final)
            return final
        except BaseException:
            # Clean up the partial / rejected tmp on ANY failure (incl. cancel).
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    @staticmethod
    def _atomic_replace(tmp: Path, final: Path) -> None:
        """``os.replace`` with one retry on PermissionError (Windows AV / lock)."""
        try:
            os.replace(tmp, final)
        except PermissionError:
            time.sleep(0.5)
            os.replace(tmp, final)

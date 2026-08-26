"""Service that auto-downloads and self-updates the yt-dlp binary.

Mirrors :mod:`anki_miner.services.update_checker`: ``urllib.request`` with a
timeout + User-Agent header, a GitHub URL allowlist, ``packaging``-based version
comparison, and a "returns a result / never raises" contract.

The binary is installed into ``~/.anki_miner/bin/`` with a verification receipt
(see :mod:`anki_miner.utils.ytdlp_resolver`). The resolver prefers an explicit
PATH install, then uses the managed binary only while its receipt still matches.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from anki_miner.config import AnkiMinerConfig, paths
from anki_miner.exceptions import OperationCancelled
from anki_miner.utils import ytdlp_resolver
from anki_miner.utils.subprocess_utils import no_window_kwargs
from anki_miner.utils.version_compare import is_newer

logger = logging.getLogger(__name__)

_STABLE_REPO = "yt-dlp/yt-dlp"
# Official nightly channel, selected by config.ytdlp_prerelease. Same publisher,
# same asset names, same SHA2-256SUMS manifest; only the repo differs. Extra
# assets it carries (yt-dlp_x86.exe, *.zip variants) are invisible to the
# exact-name matching below.
_PRERELEASE_REPO = "yt-dlp/yt-dlp-nightly-builds"


def _api_url(repo: str) -> str:
    return f"https://api.github.com/repos/{repo}/releases/latest"


def _download_prefix(repo: str) -> str:
    return f"/{repo}/releases/download/"


# Latest-release endpoint for the yt-dlp project (no auth / key — free API).
# Kept as stable-channel constants: the CDN canary and containment tests import
# them, and scripts/check_ytdlp_pin.py mirrors the stable URL.
GITHUB_API_URL = _api_url(_STABLE_REPO)

# Per-OS release asset name. Every entry MUST be a *standalone* build: the bare
# "yt-dlp" asset is a zipimport archive whose shebang runs the system python3, so
# installing it would make the app depend on a host Python it does not ship. The
# standalone builds also carry their own curl_cffi, which is what makes yt-dlp's
# --list-impersonate-targets non-empty. tests/unit/test_ytdlp_updater.py pins
# these names so a well-meaning "the small one is fine" edit fails.
_ASSET_BY_PLATFORM: dict[str, str] = {
    "linux": "yt-dlp_linux",
    "win32": "yt-dlp.exe",
    "darwin": "yt-dlp_macos",
}

_SUMS_ASSET_NAME = "SHA2-256SUMS"
_RELEASE_DOWNLOAD_PREFIX = _download_prefix(_STABLE_REPO)

# Allowlist for URLs we contact / download from. Only HTTPS on these hosts is
# accepted; everything else is fail-closed. (Mirrors update_checker's allowlist —
# copied rather than imported to keep the modules decoupled.)
#
# release-assets.githubusercontent.com is where GitHub currently 302s release
# asset downloads; objects.githubusercontent.com is the previous host, kept so an
# older or regional redirect still resolves. Both are checked only AFTER the
# redirect: the request URL itself is pinned to github.com by
# _release_tag_from_asset_url, so widening this set cannot widen what we ask for.
#
# Keep this an EXACT host set. A "*.githubusercontent.com" suffix match looks
# tempting and is a real weakening: raw. and gist. serve arbitrary user-authored
# bytes at attacker-chosen paths, whereas these two are opaque blob storage. It is
# also not covered by the SHA-256 check — the SHA2-256SUMS fetch below is itself
# guarded only by this allowlist plus TLS, so "the hash protects us" is circular
# for that leg. Omitting the current CDN host here is what silently broke every
# download between the 2026-06 ship date and this fix.
_GITHUB_URL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "api.github.com",
    }
)

# 24h throttle window for the startup background check.
_THROTTLE_SECONDS = 24 * 60 * 60
# Reject a download whose final size is below this floor (partial / garbage).
_MIN_SIZE_BYTES = 1024 * 1024  # ~1 MB
# Reject a download whose size exceeds this ceiling (endless/runaway body would
# otherwise fill the disk). The real binary is ~35 MB; 200 MB is generous
# headroom for future growth.
_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024  # 200 MB
# Streaming download chunk size.
_CHUNK_BYTES = 64 * 1024
# SHA2-256SUMS is currently tens of KiB; reject unreasonable responses.
_MAX_SUMS_BYTES = 256 * 1024
# The GitHub releases-API JSON is a few KiB; reject unreasonable responses.
_MAX_API_JSON_BYTES = 4 * 1024 * 1024  # 4 MB

# Backward-compatible module handle used by containment tests. Process users and
# updater promotion share this exact lock through ytdlp_resolver.
_INSTALL_LOCK = ytdlp_resolver._MANAGED_YTDLP_LOCK


class _PromotionDeferred(Exception):
    """Managed yt-dlp is active, so this update must retry later."""


def _validate_github_url(url: str) -> bool:
    """Return True iff *url* is an https URL on the GitHub allowlist (fail-closed)."""
    if not isinstance(url, str) or not url:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc.lower() in _GITHUB_URL_ALLOWLIST


def _release_asset_url(tag: str, asset_name: str, repo: str = _STABLE_REPO) -> str:
    """Return the canonical download URL for one yt-dlp release asset."""
    quoted_tag = urllib.parse.quote(tag, safe="")
    quoted_name = urllib.parse.quote(asset_name, safe="")
    return f"https://github.com{_download_prefix(repo)}{quoted_tag}/{quoted_name}"


def _release_tag_from_asset_url(url: str, asset_name: str, repo: str = _STABLE_REPO) -> str | None:
    """Extract a tag only from the exact release URL shape for *repo*."""
    if not isinstance(url, str):
        return None
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    prefix = _download_prefix(repo)
    suffix = f"/{urllib.parse.quote(asset_name, safe='')}"
    if (
        parts.scheme != "https"
        or parts.netloc.lower() != "github.com"
        or parts.query
        or parts.fragment
        or not parts.path.startswith(prefix)
        or not parts.path.endswith(suffix)
    ):
        return None
    quoted_tag = parts.path[len(prefix) : -len(suffix)]
    if not quoted_tag or "/" in quoted_tag:
        return None
    tag = urllib.parse.unquote(quoted_tag)
    return tag if _release_asset_url(tag, asset_name, repo) == url else None


def _manifest_sha256(manifest: bytes, asset_name: str) -> str:
    """Return the unique valid SHA-256 entry for *asset_name*, or raise."""
    entries: list[str] = []
    for line in manifest.decode("utf-8").splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        filename = fields[1]
        if filename.startswith("*"):
            filename = filename[1:]
        if filename == asset_name:
            entries.append(fields[0].lower())

    if not entries:
        raise ValueError(f"{_SUMS_ASSET_NAME} has no entry for {asset_name!r}")
    if len(entries) != 1:
        raise ValueError(f"{_SUMS_ASSET_NAME} has duplicate entries for {asset_name!r}")
    expected = entries[0]
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError(f"{_SUMS_ASSET_NAME} has an invalid SHA-256 for {asset_name!r}")
    return expected


@dataclass
class YtdlpUpdateResult:
    """Outcome of a :meth:`YtdlpUpdater.check_and_update` run.

    Attributes:
        action: One of ``"installed"``, ``"up_to_date"``, ``"skipped_throttle"``,
            ``"deferred"``, ``"unavailable"``, ``"failed"``.
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
        self._repo = _PRERELEASE_REPO if config.ytdlp_prerelease else _STABLE_REPO

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
        try:
            # Resolves (and caches) the pre-install yt-dlp path;
            # check_and_update clears the resolver cache after a successful
            # install so the next resolve picks up the fresh binary.
            executable = ytdlp_resolver.resolve_ytdlp(self._config)
            cmd = [executable, "--ignore-config", "--version"]
            with ytdlp_resolver.managed_ytdlp_lock(executable):
                proc = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
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

        Picks exactly one per-OS asset plus exactly one ``SHA2-256SUMS`` asset,
        both at canonical URLs for the reported yt-dlp repo/tag. Any failure
        yields ``(None, None)`` (or a parsed version with a None URL when only
        the release assets are invalid). Never raises.
        """
        try:
            api_url = _api_url(self._repo)
            request = urllib.request.Request(
                api_url,
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "anki-miner (+https://github.com/0xzerolight/anki_miner)",
                },
            )
            if not _validate_github_url(api_url):
                return (None, None)
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read(_MAX_API_JSON_BYTES + 1)
                if len(body) > _MAX_API_JSON_BYTES:
                    raise ValueError(f"GitHub API response exceeds the {_MAX_API_JSON_BYTES:,}-byte cap")
                data = json.loads(body.decode("utf-8"))

            tag_name = data.get("tag_name", "")
            version = tag_name.lstrip("v") or None
            if version is None:
                return (None, None)

            asset_name = _ASSET_BY_PLATFORM.get(sys.platform)
            url: str | None = None
            if asset_name:
                assets = data.get("assets") or []
                asset_candidates = [asset for asset in assets if asset.get("name") == asset_name]
                sums_candidates = [asset for asset in assets if asset.get("name") == _SUMS_ASSET_NAME]
                if len(asset_candidates) == 1 and len(sums_candidates) == 1:
                    asset_url = asset_candidates[0].get("browser_download_url")
                    sums_url = sums_candidates[0].get("browser_download_url")
                    if (
                        isinstance(asset_url, str)
                        and isinstance(sums_url, str)
                        and asset_url == _release_asset_url(tag_name, asset_name, self._repo)
                        and sums_url == _release_asset_url(tag_name, _SUMS_ASSET_NAME, self._repo)
                    ):
                        url = asset_url
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

    def _restore_throttle(self, previous_mtime_ns: int | None) -> None:
        """Undo this run's throttle touch after deferred promotion."""
        path = self._throttle_path()
        try:
            if previous_mtime_ns is None:
                path.unlink(missing_ok=True)
            else:
                os.utime(path, ns=(previous_mtime_ns, previous_mtime_ns))
        except OSError:
            logger.debug("Failed to restore yt-dlp update throttle", exc_info=True)

    def _raise_if_cancelled(self) -> None:
        if self._cancel is not None and self._cancel():
            raise OperationCancelled("yt-dlp update cancelled")

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
        previous_throttle_mtime_ns: int | None = None
        try:
            if not force and self._throttled():
                return YtdlpUpdateResult(action="skipped_throttle", message="Checked recently; skipped.")

            with ytdlp_resolver.managed_ytdlp_lock(blocking=False) as acquired:
                if not acquired:
                    return YtdlpUpdateResult(
                        action="deferred",
                        message="yt-dlp is in use; update deferred.",
                    )

            # Write the throttle BEFORE the network call so a crash / tight loop
            # does not retry-storm GitHub.
            with contextlib.suppress(OSError):
                previous_throttle_mtime_ns = self._throttle_path().stat().st_mtime_ns
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
            return YtdlpUpdateResult(
                action="installed",
                installed_version=latest,
                available_version=latest,
                path=path,
                message=f"Updated yt-dlp to {latest}.",
            )
        except _PromotionDeferred:
            self._restore_throttle(previous_throttle_mtime_ns)
            return YtdlpUpdateResult(
                action="deferred",
                message="yt-dlp is in use; update deferred.",
            )
        except OperationCancelled:
            # The user asked to stop. Not a fault, so no traceback — but the
            # action stays "failed" because that string is consumed by the GUI.
            logger.info("yt-dlp update cancelled")
            return YtdlpUpdateResult(action="failed", message="yt-dlp update cancelled.")
        except Exception as e:  # noqa: BLE001 — never propagate to the caller
            logger.exception("yt-dlp update failed")
            return YtdlpUpdateResult(action="failed", message=f"yt-dlp update failed: {e}")

    def _download_and_install(self, url: str, version: str) -> Path:
        """Stream-download *url* to a tmp file, validate, and atomically install it.

        Honors ``self._cancel`` between chunks. Cleans up the tmp on any failure.
        Returns the installed binary path. Raises on failure (the caller's
        ``check_and_update`` wraps it into a ``failed`` result).
        """
        asset_name = _ASSET_BY_PLATFORM.get(sys.platform)
        if asset_name is None:
            raise ValueError(f"No yt-dlp asset for platform {sys.platform!r}")
        tag = _release_tag_from_asset_url(url, asset_name, self._repo)
        if tag is None or tag.lstrip("v") != version:
            raise ValueError(f"Refusing non-release or mismatched yt-dlp asset URL: {url!r}")
        sums_url = _release_asset_url(tag, _SUMS_ASSET_NAME, self._repo)

        bin_dir = self.download_dir()
        bin_dir.mkdir(parents=True, exist_ok=True)
        name = self._binary_name()
        final = bin_dir / name
        fd, tmp_name = tempfile.mkstemp(prefix=f".{name}-", suffix=".tmp", dir=bin_dir)
        tmp = Path(tmp_name)

        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "anki-miner (+https://github.com/0xzerolight/anki_miner)"},
            )
            written = 0
            with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=30) as response:
                final_url = response.geturl()
                if not _validate_github_url(final_url):
                    raise ValueError(f"Refusing yt-dlp redirect to off-allowlist URL: {final_url!r}")
                while True:
                    self._raise_if_cancelled()
                    chunk = response.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                    if written > _MAX_DOWNLOAD_BYTES:
                        raise ValueError(f"Downloaded yt-dlp exceeds the {_MAX_DOWNLOAD_BYTES:,}-byte cap; aborting.")

            if written < _MIN_SIZE_BYTES:
                raise ValueError(f"Downloaded yt-dlp is implausibly small ({written} bytes); rejecting.")

            self._raise_if_cancelled()
            sums_request = urllib.request.Request(
                sums_url,
                headers={"User-Agent": "anki-miner (+https://github.com/0xzerolight/anki_miner)"},
            )
            with urllib.request.urlopen(sums_request, timeout=30) as response:
                sums_final_url = response.geturl()
                if not _validate_github_url(sums_final_url):
                    raise ValueError(f"Refusing {_SUMS_ASSET_NAME} redirect to off-allowlist URL: {sums_final_url!r}")
                manifest = response.read(_MAX_SUMS_BYTES + 1)
                if len(manifest) > _MAX_SUMS_BYTES:
                    raise ValueError(f"{_SUMS_ASSET_NAME} exceeds the {_MAX_SUMS_BYTES:,}-byte cap")
                expected_sha256 = _manifest_sha256(manifest, asset_name)
            self._raise_if_cancelled()

            with ytdlp_resolver.managed_ytdlp_lock(blocking=False) as acquired:
                if not acquired:
                    raise _PromotionDeferred
                digest = hashlib.sha256()
                with tmp.open("rb") as staged:
                    for chunk in iter(lambda: staged.read(_CHUNK_BYTES), b""):
                        self._raise_if_cancelled()
                        digest.update(chunk)
                actual_sha256 = digest.hexdigest()
                # TLS-served sums authenticate this GitHub release, not a publisher key;
                # a compromised release could replace both binary and checksum.
                if actual_sha256 != expected_sha256:
                    raise ValueError("Downloaded yt-dlp SHA-256 does not match SHA2-256SUMS")

                if sys.platform != "win32":
                    os.chmod(tmp, 0o755)
                    if sys.platform == "darwin":
                        # Best-effort: strip the quarantine xattr so Gatekeeper does
                        # not block the freshly-downloaded binary. Failure is fine.
                        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                            subprocess.run(
                                ["xattr", "-d", "com.apple.quarantine", str(tmp)],
                                stdin=subprocess.DEVNULL,
                                capture_output=True,
                                timeout=10,
                                **no_window_kwargs(),
                            )

                self._raise_if_cancelled()
                self._promote_verified_binary(tmp, final, actual_sha256)
                ytdlp_resolver._clear_cache()
                from anki_miner.services import ytdlp_invocation

                ytdlp_invocation.ytdlp_supports_js_runtimes.cache_clear()
                ytdlp_invocation.ytdlp_supports_remote_components.cache_clear()
            logger.info("Installed yt-dlp %s to %s", version, final)
            return final
        except BaseException:
            # Clean up the partial / rejected tmp on ANY failure (incl. cancel).
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    def _promote_verified_binary(self, staged: Path, final: Path, sha256: str) -> None:
        """Publish binary + receipt as one rollback-safe managed generation."""
        receipt = ytdlp_resolver.ytdlp_verification_receipt_path(final)
        binary_backup = final.with_name(f".{final.name}.{os.getpid()}.rollback")
        receipt_backup = receipt.with_name(f".{receipt.name}.{os.getpid()}.rollback")
        binary_existed = final.exists()
        receipt_existed = receipt.exists()
        binary_backed_up = False
        receipt_backed_up = False
        try:
            if binary_existed:
                self._atomic_replace(final, binary_backup)
                binary_backed_up = True
            if receipt_existed:
                self._atomic_replace(receipt, receipt_backup)
                receipt_backed_up = True
            self._atomic_replace(staged, final)
            self._write_verification_receipt(final, sha256)
        except BaseException:
            if binary_backed_up:
                self._atomic_replace(binary_backup, final)
            elif not binary_existed:
                with contextlib.suppress(OSError):
                    final.unlink()
            if receipt_backed_up:
                self._atomic_replace(receipt_backup, receipt)
            elif not receipt_existed:
                with contextlib.suppress(OSError):
                    receipt.unlink()
            raise
        else:
            with contextlib.suppress(OSError):
                binary_backup.unlink()
            with contextlib.suppress(OSError):
                receipt_backup.unlink()

    def _write_verification_receipt(self, binary: Path, sha256: str) -> None:
        """Atomically record the verified digest beside a promoted binary."""
        receipt = ytdlp_resolver.ytdlp_verification_receipt_path(binary)
        tmp = receipt.with_name(f"{receipt.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(f"{sha256}\n", encoding="ascii")
            self._atomic_replace(tmp, receipt)
        except BaseException:
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

"""Live canary: the real GitHub release-asset redirect must stay on the allowlist.

This exists because of a silent, month-long outage. ``ytdlp_updater`` validates the
URL a release download *lands on* (``response.geturl()``) against an exact host
allowlist. GitHub migrated release-asset delivery to
``release-assets.githubusercontent.com``; the allowlist still named only the old
``objects.githubusercontent.com``, so every yt-dlp download was refused on every
platform from the day the updater shipped. The unit suite could not catch it —
every fake response returned a hardcoded allowlisted host, so the real host was
never exercised.

Unit tests pin the allowlist's *contents*. Only this test pins the allowlist
against *reality*, which is where the bug lived.

Requires both markers: ``youtube`` (excluded from the default gate, like every
other network-dependent test here) and ``network``
(``tests/_network_tripwire.py`` blocks real TCP without it).

Run locally with::

    pytest -m "youtube and network" tests/integration/test_ytdlp_release_cdn_canary.py

CI runs it on a weekly schedule (``.github/workflows/ytdlp-cdn-canary.yml``)
rather than on every push, so a GitHub or yt-dlp hiccup cannot red an unrelated PR.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services import ytdlp_updater
from anki_miner.services.ytdlp_updater import YtdlpUpdater

pytestmark = [pytest.mark.youtube, pytest.mark.network]

_USER_AGENT = "anki-miner-canary (+https://github.com/0xzerolight/anki_miner)"

_FIX_HINT = (
    "Add the exact new host to _GITHUB_URL_ALLOWLIST in "
    "anki_miner/services/ytdlp_updater.py AND anki_miner/services/update_checker.py. "
    "Do NOT widen it to a *.githubusercontent.com suffix match — raw. and gist. "
    "serve arbitrary user-authored bytes (see the rationale comment on the allowlist)."
)


def _resolve_final_url(url: str) -> str:
    """Follow redirects the way ``_download_and_install`` does and return the landing URL."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed https GitHub URL
        return response.geturl()


def test_release_api_host_is_on_the_allowlist() -> None:
    """The latest-release API endpoint must still resolve to an allowlisted host."""
    try:
        final_url = _resolve_final_url(ytdlp_updater.GITHUB_API_URL)
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - network flake
        pytest.skip(f"GitHub API unreachable: {exc}")

    assert ytdlp_updater._validate_github_url(
        final_url
    ), f"GitHub API host drifted off the allowlist: {final_url!r}.\n{_FIX_HINT}"


def test_real_asset_download_redirect_is_on_the_allowlist() -> None:
    """Follow a real release-asset download and validate where it lands.

    This is the exact assertion ``_download_and_install`` makes at runtime, against
    the exact URL shape it builds. A failure here means every in-app yt-dlp install
    and update is currently refused for every user.
    """
    updater = YtdlpUpdater(AnkiMinerConfig())

    try:
        version, asset_url = updater.latest_version_and_asset()
    except Exception as exc:  # pragma: no cover - network flake  # noqa: BLE001
        pytest.skip(f"could not resolve the latest release: {exc}")

    if not version or not asset_url:
        pytest.skip("GitHub did not return a usable latest-release asset")

    try:
        final_url = _resolve_final_url(asset_url)
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - network flake
        pytest.skip(f"asset download unreachable: {exc}")

    assert ytdlp_updater._validate_github_url(
        final_url
    ), f"GitHub release-asset CDN host drifted off the allowlist: {final_url!r}.\n{_FIX_HINT}"


def test_sums_manifest_redirect_is_on_the_allowlist() -> None:
    """The SHA2-256SUMS fetch is guarded by the allowlist alone, so pin it too.

    Unlike the binary, the manifest has no hash to fall back on — it *is* the
    trust anchor — so this leg failing is what makes the "the SHA-256 check
    protects us" argument circular. It gets its own assertion.
    """
    updater = YtdlpUpdater(AnkiMinerConfig())

    try:
        version, asset_url = updater.latest_version_and_asset()
    except Exception as exc:  # pragma: no cover - network flake  # noqa: BLE001
        pytest.skip(f"could not resolve the latest release: {exc}")

    if not version or not asset_url:
        pytest.skip("GitHub did not return a usable latest-release asset")

    asset_name = ytdlp_updater._ASSET_BY_PLATFORM.get(sys.platform)
    if asset_name is None:
        pytest.skip(f"no yt-dlp asset configured for platform {sys.platform!r}")

    tag = ytdlp_updater._release_tag_from_asset_url(asset_url, asset_name)
    if tag is None:
        pytest.skip(f"asset URL shape not recognized for this platform: {asset_url!r}")

    sums_url = ytdlp_updater._release_asset_url(tag, ytdlp_updater._SUMS_ASSET_NAME)

    try:
        final_url = _resolve_final_url(sums_url)
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - network flake
        pytest.skip(f"SHA2-256SUMS unreachable: {exc}")

    assert ytdlp_updater._validate_github_url(
        final_url
    ), f"SHA2-256SUMS CDN host drifted off the allowlist: {final_url!r}.\n{_FIX_HINT}"

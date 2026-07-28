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

import functools
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services import ytdlp_updater
from anki_miner.services.ytdlp_updater import YtdlpUpdater

pytestmark = [pytest.mark.youtube, pytest.mark.network]

_USER_AGENT = "anki-miner-canary (+https://github.com/0xzerolight/anki_miner)"

#: GitHub's documented unauthenticated REST limit, shared per source IP — which on a
#: hosted runner means shared with every other job on that machine.
_UNAUTHENTICATED_RATE_LIMIT = 60

_FIX_HINT = (
    "Add the exact new host to _GITHUB_URL_ALLOWLIST in "
    "anki_miner/services/ytdlp_updater.py AND anki_miner/services/update_checker.py. "
    "Do NOT widen it to a *.githubusercontent.com suffix match — raw. and gist. "
    "serve arbitrary user-authored bytes (see the rationale comment on the allowlist)."
)


class _ApiTokenHandler(urllib.request.BaseHandler):
    """Attach the CI token to api.github.com requests, and to nothing else."""

    def __init__(self, token: str) -> None:
        self._token = token

    def https_request(self, request: urllib.request.Request) -> urllib.request.Request:
        if request.host == "api.github.com":
            # add_unredirected_header, NEVER add_header. HTTPRedirectHandler copies
            # req.headers into the redirected request before the processor chain runs
            # again, so a plain header outlives the host check and follows the redirect
            # to whatever host GitHub picks next. The host gate is defence in depth;
            # this call is the control. "GitHub does not redirect that endpoint" is
            # exactly the assumption this whole canary exists to distrust.
            request.add_unredirected_header("Authorization", f"Bearer {self._token}")
        return request


@pytest.fixture(scope="module", autouse=True)
def _authenticated_github_api() -> Iterator[None]:
    """Make the workflow's ``GITHUB_TOKEN`` actually raise the API rate limit.

    The canary sets ``GITHUB_TOKEN`` in its env but nothing ever read it: this file
    sends only ``User-Agent`` and ``latest_version_and_asset`` only adds ``Accept``.
    So the whole run shared the unauthenticated 60/hr bucket with every other job on
    the runner, and a 403 skipped every leg. Now that a skipped leg reds the canary,
    that would have been a weekly false alarm.

    Installing a global opener reaches production code too — ``latest_version_and_asset``
    calls the module-level ``urllib.request.urlopen`` — so no source change is needed.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        yield
        return

    previous = urllib.request._opener
    urllib.request.install_opener(urllib.request.build_opener(_ApiTokenHandler(token)))
    try:
        yield
    finally:
        urllib.request._opener = previous


def _resolve_final_url(url: str) -> str:
    """Follow redirects the way ``_download_and_install`` does and return the landing URL."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed https GitHub URL
        return response.geturl()


def _api_diagnosis() -> str:
    """Say *why* api.github.com is uncooperative, for the skip and failure messages.

    ``latest_version_and_asset`` never raises — it swallows everything into
    ``(None, None)`` — so without this probe an auth or rate-limit failure surfaces as
    a bare "no usable asset", a red signal whose text asserts a cause nothing checked.
    That is the same disease as issue #104.
    """
    request = urllib.request.Request(ytdlp_updater.GITHUB_API_URL, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https GitHub URL
            remaining = response.headers.get("X-RateLimit-Remaining", "?")
            limit = response.headers.get("X-RateLimit-Limit", "?")
            return f"HTTP {response.status}, rate limit {remaining}/{limit}"
    except urllib.error.HTTPError as exc:
        headers = exc.headers or {}
        remaining = headers.get("X-RateLimit-Remaining", "?")
        limit = headers.get("X-RateLimit-Limit", "?")
        return f"HTTP {exc.code} {exc.reason}, rate limit {remaining}/{limit}"
    except (urllib.error.URLError, OSError) as exc:
        return f"unreachable: {exc}"


@pytest.fixture(scope="module")
def resolve_latest_release() -> Callable[[], tuple[str, str]]:
    """Resolve the latest release once, shared by the two legs that need it.

    Both legs used to call this independently, on top of leg 1's own request, and each
    carried the same skip preamble.

    Returns a callable rather than the tuple itself, deliberately: conftest's
    ``_network_guard`` is *function*-scoped, so a module-scoped fixture is set up
    before it flips ``_network_tripwire.SUPPRESSED`` and any I/O at fixture-setup time
    is blocked outright. Resolving lazily keeps the request inside the test's own
    suppression window while still doing it only once for the module.
    """

    @functools.cache
    def resolve() -> tuple[str, str]:
        version, asset_url = YtdlpUpdater(AnkiMinerConfig()).latest_version_and_asset()
        if not version or not asset_url:
            pytest.skip(f"GitHub returned no usable latest-release asset ({_api_diagnosis()})")
        return version, asset_url

    return resolve


def test_github_token_is_applied_when_present() -> None:
    """Guard the auth fixture against going inert.

    ``3 passed`` is also what you see when the opener was never installed, the hook is
    misnamed, or the header name is wrong — so assert the rate-limit headroom directly
    rather than inferring it from the other legs passing.
    """
    request = urllib.request.Request(ytdlp_updater.GITHUB_API_URL, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https GitHub URL
            limit = int(response.headers.get("X-RateLimit-Limit", "0"))
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - network flake
        pytest.skip(f"GitHub API unreachable: {exc}")

    if os.environ.get("GITHUB_TOKEN"):
        assert limit > _UNAUTHENTICATED_RATE_LIMIT, (
            f"GITHUB_TOKEN is set but api.github.com still reports a {limit}/hr limit — "
            "the auth fixture is inert, so the canary is back on the shared 60/hr bucket"
        )
    else:
        assert limit <= _UNAUTHENTICATED_RATE_LIMIT, (
            f"no GITHUB_TOKEN, but api.github.com reports a {limit}/hr limit — "
            "something is authenticating these requests unexpectedly"
        )


def test_release_api_host_is_on_the_allowlist() -> None:
    """The latest-release API endpoint must still resolve to an allowlisted host."""
    try:
        final_url = _resolve_final_url(ytdlp_updater.GITHUB_API_URL)
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - network flake
        pytest.skip(f"GitHub API unreachable: {exc}")

    assert ytdlp_updater._validate_github_url(
        final_url
    ), f"GitHub API host drifted off the allowlist: {final_url!r}.\n{_FIX_HINT}"


def test_real_asset_download_redirect_is_on_the_allowlist(
    resolve_latest_release: Callable[[], tuple[str, str]],
) -> None:
    """Follow a real release-asset download and validate where it lands.

    This is the exact assertion ``_download_and_install`` makes at runtime, against
    the exact URL shape it builds. A failure here means every in-app yt-dlp install
    and update is currently refused for every user.
    """
    _version, asset_url = resolve_latest_release()

    try:
        final_url = _resolve_final_url(asset_url)
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - network flake
        pytest.skip(f"asset download unreachable: {exc}")

    assert ytdlp_updater._validate_github_url(
        final_url
    ), f"GitHub release-asset CDN host drifted off the allowlist: {final_url!r}.\n{_FIX_HINT}"


def test_sums_manifest_redirect_is_on_the_allowlist(
    resolve_latest_release: Callable[[], tuple[str, str]],
) -> None:
    """The SHA2-256SUMS fetch is guarded by the allowlist alone, so pin it too.

    Unlike the binary, the manifest has no hash to fall back on — it *is* the
    trust anchor — so this leg failing is what makes the "the SHA-256 check
    protects us" argument circular. It gets its own assertion.
    """
    _version, asset_url = resolve_latest_release()

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

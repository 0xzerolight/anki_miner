#!/usr/bin/env python3
"""Gate the vendored yt-dlp pin on freshness.

yt-dlp ships roughly monthly and breaks whenever YouTube changes something, so a
bundle carrying a months-old binary ships a YouTube tab that is already degraded.
Nothing else catches a forgotten bump: ``dependabot.yml`` covers ``pip`` and
``github-actions`` only, neither of which can see a curl'd release asset URL.

Exit codes:
  0  pin is fresh, OR the check could not be performed (see below)
  1  pin is definitively stale, or the pin file itself is malformed

**Fails closed only on a definitive answer.** If GitHub is unreachable or
rate-limited, this warns and exits 0 rather than reding the build.
``scripts/release_preflight.sh`` runs locally with no token, and unauthenticated
``api.github.com`` allows 60 requests/hour per IP — under the project's
"never tag on a red or unrun dry-run" rule, a hard fail there would let a transient
API hiccup block releases outright. Set ``GITHUB_TOKEN`` on CI legs to make the
check reliable rather than best-effort.

Usage:
    python scripts/check_ytdlp_pin.py [--pin .github/ytdlp-pin.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PIN = _REPO_ROOT / ".github" / "ytdlp-pin.json"
_LATEST_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
_USER_AGENT = "anki-miner-pin-check (+https://github.com/0xzerolight/anki_miner)"


def _parse_version_date(version: str) -> date:
    """yt-dlp tags are ``YYYY.MM.DD``, so the tag dates the release directly."""
    return datetime.strptime(version.strip(), "%Y.%m.%d").date()


def _fetch_latest_tag() -> str | None:
    """Return the latest yt-dlp tag, or None when GitHub could not be reached."""
    request = urllib.request.Request(_LATEST_URL, headers={"User-Agent": _USER_AGENT})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https URL
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"WARNING: could not reach the GitHub releases API ({exc}).")
        return None
    tag = payload.get("tag_name")
    return tag if isinstance(tag, str) and tag else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", type=Path, default=_DEFAULT_PIN, help="path to ytdlp-pin.json")
    args = parser.parse_args(argv)

    try:
        pin = json.loads(args.pin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL: cannot read the yt-dlp pin at {args.pin}: {exc}")
        return 1

    version = pin.get("version")
    if not isinstance(version, str):
        print(f"FAIL: {args.pin} has no string 'version'.")
        return 1

    try:
        pinned_date = _parse_version_date(version)
    except ValueError:
        print(f"FAIL: pinned yt-dlp version {version!r} is not a YYYY.MM.DD tag.")
        return 1

    # Guard the asset table too: a zipapp entry here would ship a bundle that
    # depends on a host Python, which is the whole reason for pinning standalone
    # builds. Cheap to check, and it needs no network.
    assets = pin.get("assets")
    if not isinstance(assets, dict) or not assets:
        print(f"FAIL: {args.pin} has no 'assets' table.")
        return 1
    for leg, entry in assets.items():
        if not isinstance(entry, dict):
            print(f"FAIL: assets.{leg} is not an object.")
            return 1
        asset = entry.get("asset")
        if asset == "yt-dlp":
            print(
                f"FAIL: assets.{leg} pins the bare 'yt-dlp' zipapp asset. "
                "Use a standalone build (yt-dlp_linux / yt-dlp.exe / yt-dlp_macos): "
                "the zipapp runs the system python3 and carries no curl_cffi."
            )
            return 1
        sha = entry.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64:
            print(f"FAIL: assets.{leg} has no 64-character sha256.")
            return 1

    max_age = pin.get("max_age_days", 90)
    if not isinstance(max_age, int) or max_age <= 0:
        print(f"FAIL: {args.pin} has an invalid 'max_age_days'.")
        return 1

    age_days = (date.today() - pinned_date).days
    latest = _fetch_latest_tag()

    if latest is None:
        # Best-effort mode: no authoritative answer, so never red the build. Still
        # report the local age so a badly stale pin is visible in the log.
        print(f"WARNING: yt-dlp pin freshness unverified (pinned {version}, {age_days} days old).")
        return 0

    if latest == version:
        print(f"OK: yt-dlp pin {version} is the latest release.")
        return 0

    if age_days <= max_age:
        print(f"OK: yt-dlp pin {version} is {age_days} days old (latest {latest}, limit {max_age} days).")
        return 0

    print(
        f"FAIL: yt-dlp pin {version} is {age_days} days old (limit {max_age}); "
        f"latest release is {latest}.\n"
        "Bump 'version' and every sha256 in .github/ytdlp-pin.json before releasing. "
        "Digests come from that release's SHA2-256SUMS:\n"
        f"  gh release download {latest} --repo yt-dlp/yt-dlp --pattern SHA2-256SUMS"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

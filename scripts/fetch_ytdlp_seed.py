#!/usr/bin/env python3
"""Seed the app-managed yt-dlp the release bundle smoke needs.

The bundle ships no yt-dlp: it arrives as an in-app download into
``ANKI_MINER_HOME/bin/`` (``anki_miner/services/ytdlp_updater.py``), verified
against its release's ``SHA2-256SUMS`` and recorded by a ``.verified`` receipt
the resolver re-checks on every resolve. The youtube bundle smoke asserts that
the RESOLVER picks that slot, so it has to be HANDED one.

This drives the app's own updater rather than curling the asset itself -- no URL,
digest or promotion rule is duplicated, which is what the hand-written vendoring
steps this replaces got wrong once per platform. ``scripts/bundle_smoke.sh``
copies ``<dest>/bin/`` into its isolated ``ANKI_MINER_HOME`` before the leg runs.

Failure policy, mirroring ``scripts/fetch_language_pack_seeds.py``:

* A download that never completed (GitHub outage, DNS, 4xx) warns loudly and
  exits 0. The bundle is correct; the fetch is not, and a release must not go red
  over someone else's outage. The smoke then reports ``SKIP youtube``.
* Anything else -- a digest mismatch above all -- exits 1. Those say the bytes are
  wrong, and smoking against them proves nothing.

Usage:
    python scripts/fetch_ytdlp_seed.py <dest_dir>
"""

from __future__ import annotations

import argparse
import http.client
import os
import sys
import urllib.error
from pathlib import Path

# scripts/ is on sys.path when this runs as `python scripts/…`, the repo root is
# not; the release job pip-installs the package, but keep a source checkout
# runnable too.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: The release the seed installs. Pinned for CI stability, not for freshness: the
#: leg proves the resolver picks the app-managed slot, and release assets are
#: immutable. Nothing gates its age, and nothing should -- the shipping app
#: fetches the LATEST release at run time, which is what freshness means here.
PINNED_TAG = "2026.08.19"


def _updater_factory():
    """Build the app's own updater. A seam so the tests need no network."""
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.ytdlp_updater import YtdlpUpdater

    return YtdlpUpdater(AnkiMinerConfig())


def seed(dest: Path) -> int:
    """Install the pinned yt-dlp into ``dest/bin`` with its receipt."""
    # paths.ANKI_MINER_HOME is read at IMPORT time, so the env var alone is not
    # enough once anki_miner is already imported (the tests) -- rebind both.
    os.environ["ANKI_MINER_HOME"] = str(dest)
    from anki_miner.config import paths
    from anki_miner.services.ytdlp_updater import _ASSET_BY_PLATFORM, _release_asset_url

    # _ASSET_BY_PLATFORM and _release_asset_url are imported private rather than
    # reimplemented: the asset table is the standalone-vs-zipapp rule (a zipapp
    # asset would shebang a system python the bundle does not ship and carry no
    # curl_cffi), and a second copy would answer a different question the moment
    # either drifts.
    paths.ANKI_MINER_HOME = Path(dest)

    asset = _ASSET_BY_PLATFORM.get(sys.platform)
    if asset is None:
        print(f"::error::no yt-dlp release asset for platform {sys.platform!r}", flush=True)
        return 1

    url = _release_asset_url(PINNED_TAG, asset)
    print(f"Seeding yt-dlp {PINNED_TAG} ({asset}) into {dest}/bin", flush=True)
    try:
        installed = _updater_factory()._download_and_install(url, PINNED_TAG)
    except urllib.error.HTTPError as exc:
        # A 4xx is not an outage: the tag or the asset name is wrong, and every
        # later release would skip the youtube leg in silence. Fail closed.
        # HTTPError subclasses URLError, so this branch MUST come first.
        if 400 <= exc.code < 500:
            print(
                f"::error::yt-dlp seed asset {url} returned HTTP {exc.code} - fix PINNED_TAG or the asset name",
                flush=True,
            )
            return 1
        print(
            f"::warning::yt-dlp seed download failed (HTTP {exc.code}) - the youtube "
            "bundle smoke will be skipped. NOT failing the release.",
            flush=True,
        )
        return 0
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
        # HTTPException (IncompleteRead and friends) is NOT an OSError, so it has
        # to be named: a truncated transfer is an outage, not wrong bytes.
        print(
            f"::warning::yt-dlp seed download failed ({exc}) - the youtube bundle "
            "smoke will be skipped. NOT failing the release.",
            flush=True,
        )
        return 0
    except Exception as exc:
        # Everything left is deterministic and wrong -- the digest mismatch above all.
        print(f"::error::yt-dlp seed failed: {exc}", flush=True)
        return 1

    print(f"Seeded {installed} ({installed.stat().st_size} bytes) + its .verified receipt", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", type=Path, help="seed home; the binary lands in <dest>/bin/")
    args = parser.parse_args(argv)
    return seed(args.dest)


if __name__ == "__main__":
    sys.exit(main())

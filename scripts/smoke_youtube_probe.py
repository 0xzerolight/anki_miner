"""Smoke test: verify YouTubeFetcherService probes a public short video.

Runs against the installed wheel (NOT against the PyInstaller binary — the
bundled GUI exe has no arbitrary-script entrypoint by design). Pair this with
the Qt-headless bundled-binary smoke job in release.yml.

Exit codes:
  0 - probe succeeded and returned a plausible VideoInfo
  1 - probe failed
"""

from __future__ import annotations

import sys

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.services.youtube_fetcher import YouTubeFetcherService

# Short public video. Pick something tiny and durable; PSY Gangnam Style is
# widely available, cache-friendly, and unlikely to disappear.
SMOKE_URL = "https://www.youtube.com/watch?v=9bZkp7q19f0"


def main() -> int:
    config = AnkiMinerConfig()
    fetcher = YouTubeFetcherService(config)
    try:
        info = fetcher.probe_metadata(SMOKE_URL)
    except Exception as exc:
        print(f"SMOKE FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not info.video_id:
        print("SMOKE FAIL: VideoInfo has empty video_id", file=sys.stderr)
        return 1

    print(f"SMOKE PASS: id={info.video_id} title={info.title!r} duration={info.duration_s}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

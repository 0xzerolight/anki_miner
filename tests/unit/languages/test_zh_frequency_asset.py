"""The published opensubtitles-zh-word asset is the one the converter built (ZH-005)."""

from __future__ import annotations

import hashlib
import urllib.request

import pytest

from anki_miner.languages.zh.catalog import OPENSUBTITLES_ZH_WORD_URL

#: sha256 and byte count of opensubtitles-zh-word-2026.09.20.zip as built by
#: scripts/build_zh_frequency.py and published under the resources-2026-09-21
#: tag. A versioned filename is never re-uploaded, so a mismatch means the asset
#: was replaced.
ASSET_SHA256 = "991da78003cf1b2c9da4022ed694b9394d6496e171f76b5c79abebd71c46ccbb"
ASSET_BYTES = 400970


@pytest.mark.network
def test_the_published_asset_still_hashes_to_the_pinned_value():
    with urllib.request.urlopen(OPENSUBTITLES_ZH_WORD_URL, timeout=60) as response:  # noqa: S310 - pinned https
        payload = response.read()
    assert len(payload) == ASSET_BYTES
    assert hashlib.sha256(payload).hexdigest() == ASSET_SHA256

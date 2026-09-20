"""The published opensubtitles-vi-word asset is the one the converter built (DECIDED 3)."""

from __future__ import annotations

import hashlib
import urllib.request

import pytest

from anki_miner.languages.vi.catalog import OPENSUBTITLES_VI_WORD_URL

#: sha256 and byte count of opensubtitles-vi-word-2026.09.19.zip as built by
#: scripts/build_vi_frequency.py and published under the resources-2026-09-20
#: tag. A versioned filename is never re-uploaded, so a mismatch means the asset
#: was replaced.
ASSET_SHA256 = "4475a9a3c9ee8247a60f11c9fe395f8212afa86296c296b24c452e111dd8d896"
ASSET_BYTES = 402194


@pytest.mark.network
def test_the_published_asset_still_hashes_to_the_pinned_value():
    with urllib.request.urlopen(OPENSUBTITLES_VI_WORD_URL, timeout=60) as response:  # noqa: S310 - pinned https
        payload = response.read()
    assert len(payload) == ASSET_BYTES
    assert hashlib.sha256(payload).hexdigest() == ASSET_SHA256

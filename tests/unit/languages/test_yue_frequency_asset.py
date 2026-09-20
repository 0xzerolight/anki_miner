"""The published Cantonese frequency asset is byte-identical to what was built."""

import hashlib
import urllib.request

import pytest

from anki_miner.languages.yue.catalog import YUE_CATALOG

#: sha256 of the asset built by scripts/build_yue_frequency.py and published
#: under the resources-2026-09-20 tag. A versioned filename is never
#: re-uploaded, so a mismatch means the asset was replaced.
EXPECTED = "604ce69f9793f46f46c31d99ffa6913def344e3cac467d118ca04a94a09f7029"
SIZE = 909116


@pytest.mark.network
def test_the_published_asset_still_hashes_to_the_pinned_value():
    (spec,) = [spec for spec in YUE_CATALOG if spec.kind == "freq"]
    with urllib.request.urlopen(spec.url, timeout=60) as response:  # noqa: S310 - a pinned https release asset
        payload = response.read()
    assert len(payload) == SIZE
    assert hashlib.sha256(payload).hexdigest() == EXPECTED

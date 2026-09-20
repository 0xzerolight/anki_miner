"""The published Thai frequency assets are byte-identical to what was built."""

import hashlib
import urllib.request

import pytest

from anki_miner.languages.th.catalog import TH_CATALOG

#: sha256 of the assets built by scripts/convert_tnc_thai_frequency.py and
#: published under the resources-2026-09-20 tag. A versioned filename is never
#: re-uploaded, so a mismatch means the asset was replaced.
EXPECTED = {
    "tnc-th": "dc38af9e6c016b6ff165a863f969ec384bb5c05f9df98ee961b0eb870585756c",
    "ttc-th": "90a97a56f1b5375bb10b7fa9e36a79bd7fef2659d8525fa889a74e0a281e98ff",
}
SIZES = {"tnc-th": 688439, "ttc-th": 125981}


@pytest.mark.network
@pytest.mark.parametrize("spec", [s for s in TH_CATALOG if s.kind == "freq"], ids=lambda s: s.id)
def test_the_published_asset_still_hashes_to_the_pinned_value(spec):
    with urllib.request.urlopen(spec.url, timeout=60) as response:  # noqa: S310 - a pinned https release asset
        payload = response.read()
    assert len(payload) == SIZES[spec.id]
    assert hashlib.sha256(payload).hexdigest() == EXPECTED[spec.id]

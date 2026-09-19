"""Live probe: the Edge read-aloud endpoint still speaks a word for the pinned token scheme.

The unit suite scripts the WebSocket, so it cannot see Microsoft rotate the
trusted-client token or the Sec-MS-GEC scheme (spec §8, "Edge read-aloud token
scheme rotates"); only this test can. It speaks with a stub voice rather than a
mining language's: the question is whether the protocol still works.

Markers ``youtube`` + ``network``, the CDN-canary precedent
(tests/integration/test_ytdlp_release_cdn_canary.py). ``youtube`` is here only
because it is the default gate's exclusion marker (``-m "not youtube and …"``
in scripts/health.sh and .github/workflows/ci.yml); this test has nothing to do
with YouTube, and ``network`` alone would put a live Microsoft endpoint in
every gate run. ``network`` lets the socket past the tripwire. Run it with::

    pytest -m "youtube and network" tests/integration/test_edge_tts_live.py -p no:cacheprovider
"""

from __future__ import annotations

import pytest

from anki_miner.services.audio_fetch_common import is_mp3
from anki_miner.services.edge_tts_audio_fetcher import EdgeTtsAudioFetcher

pytestmark = [pytest.mark.youtube, pytest.mark.network]


def test_one_word_is_synthesised(tmp_path):
    fetcher = EdgeTtsAudioFetcher(
        cache_dir=tmp_path, delay=0, voice="en-US-AriaNeural", speakable=lambda term, reading: term
    )

    path = fetcher.fetch("hello", "")

    assert path is not None, f"no audio; failure tallies {fetcher.stats()} (log: 'Audio fetch: source=edgetts')"
    body = path.read_bytes()
    assert is_mp3(body)
    assert len(body) > 1000

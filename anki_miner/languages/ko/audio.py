"""Korean audio defaults: word audio and sentence TTS.

Word audio defaults to Google Translate TTS with tl=ko. JPod101 is not in the
chain: its endpoint path is literally /dictionary/japanese/, so it can only ever
answer Japanese. There is no KoreanClass101 entry either, and this is settled
rather than pending: the endpoint family does not generalise. The Korean bucket
prefix answers an S3 AccessDenied at the transport level, not an
application-level miss, so no per-word URL under it can resolve.

The ladder puts the ORTHOGRAPHIC form in the reading slot: the Google fetcher
synthesises from that slot, and Korean TTS applies its own phonology, so 국물 is
what should be spoken. The pronunciation respelling (궁물) is a second rung, used
only when the first fails.

cache_stem_prefix namespaces the stem (googletts_ko_...) because the stem doubles
as the Anki media filename: without it a Korean and a Japanese card for the same
han spelling would share one audio file. sentence_cache_stem_prefix does the same
for sentence TTS. Japanese stems are unchanged.

Sentence TTS uses Papago's native Korean voice. kyuri is the Korean speaker id,
sibling of the Japanese yuri already used at sentence_tts_fetcher.py:68, and it
is live-confirmed: the endpoint returns real Korean audio for it.
"""

from __future__ import annotations

from typing import Any

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages.profile import AudioDefaults


def ko_audio_candidates(word: Any) -> list[tuple[str, str]]:
    """Ordered (term, speakable-text) pairs for one word."""
    term = str(getattr(word, "mined_form", "") or "")
    if not term:
        return []
    reading = str(getattr(word, "expression_reading", "") or "")
    pairs = [(term, term)]
    if reading and reading != term:
        pairs.append((term, reading))
    return pairs


KO_AUDIO = AudioDefaults(
    gtts_lang="ko",
    cache_stem_prefix="googletts_ko",
    sentence_cache_stem_prefix="sentencetts_ko",
    custom_fetcher_language="ko",
    papago_speaker="kyuri",
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=ko_audio_candidates,
)

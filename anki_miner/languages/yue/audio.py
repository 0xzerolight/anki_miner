"""yue expression- and sentence-audio defaults (spec F.1).

Google Translate carries a Cantonese voice -- gTTS 2.5.4's ``tts_langs()`` lists
``yue`` -- so the default chain is the one synthetic leg the app already ships.
A user adds an audio pack or a custom_json server ahead of it exactly as on the
JA side.

The spec's chain starts with a Wiktionary recordings leg; batch C ships no
Wiktionary fetcher (owner decision 1, no Stage W), so the chain starts at the
next leg and no ``wiktionary_audio`` capability is declared.

``edge_voice`` is the Microsoft Edge read-aloud voice the user can ADD as a
second synthetic leg (Settings -> Word Audio -> Add audio source... -> Online
Source...). It is profile data only: the seam builds the leg when the chain
holds an ``edgetts`` entry, and yue's default chain does not. ``zh-HK-HiuMaanNeural``
was probed live by the seam owner; ``zh-HK-HiuGaaiNeural`` and
``zh-HK-WanLungNeural`` are the other two Hong Kong voices.

Cache stems are namespaced (``googletts_yue`` / ``sentencetts_yue``) because the
caches are keyed by term and reading only -- a yue card for 我 and a zh card for
我 would otherwise share one file.
"""

from __future__ import annotations

from typing import Any

from anki_miner.config import AudioSourceEntry
from anki_miner.languages.profile import AudioDefaults


def yue_audio_candidates(word: Any) -> list[tuple[str, str]]:
    """The single ``(term, reading)`` query pair for the yue audio ladder.

    One pair, unlike zh's two: yue is traditional-only in v1, so a word has one
    spelling and there is no other-script variant worth a second request.
    """
    term = getattr(word, "mined_form", "") or ""
    if not term:
        return []
    return [(term, getattr(word, "expression_reading", "") or "")]


def yue_speakable(term: str, reading: str) -> str | None:
    """What Google TTS speaks for a Cantonese pair: the characters, never the jyutping.

    The reading slot holds jyutping, which a ``yue`` voice reads as Latin
    letters; the term is what it can pronounce. The cache stem still carries the
    jyutping, keeping homographs apart.
    """
    return term or None


YUE_AUDIO = AudioDefaults(
    gtts_lang="yue",
    cache_stem_prefix="googletts_yue",
    sentence_cache_stem_prefix="sentencetts_yue",
    custom_fetcher_language="yue",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=yue_audio_candidates,
    speakable=yue_speakable,
    edge_voice="zh-HK-HiuMaanNeural",
)

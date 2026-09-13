"""zh expression- and sentence-audio defaults (spec 9.1 / 10.1).

No JPod101 equivalent ships in the default chain: the surveyed Chinese sources
are either paid or unstable, so the one default entry is the synthetic Google
Translate voice the app already carries for JA. A user adds an audio pack or a
custom_json server ahead of it exactly as on the JA side.

Cache stems are namespaced (``googletts_zh`` / ``sentencetts_zh``) because the
caches are keyed by term and reading only — a zh card for 我 and a ja card for
我 would otherwise share one file.
"""

from __future__ import annotations

from typing import Any

from anki_miner.config import AudioSourceEntry
from anki_miner.languages.profile import AudioDefaults
from anki_miner.languages.zh import variants


def zh_audio_candidates(word: Any) -> list[tuple[str, str]]:
    """Ordered ``(term, reading)`` query pairs for the zh audio retry ladder.

    The ja ladder retries okurigana-only lemma variants; Chinese has no
    inflection, so the only alternate spelling worth a second request is the
    other script. Every variant carries the SAME pinyin reading — simplified and
    traditional differ in glyph, never in pronunciation — so a traditional pack
    hits on a simplified front and vice versa. Empty terms are dropped and
    duplicates collapse, so a single-script word issues exactly one request.
    """
    term = getattr(word, "mined_form", "") or ""
    reading = getattr(word, "expression_reading", "") or ""
    if not term:
        return []
    pairs: list[tuple[str, str]] = []
    for candidate in variants.variant_candidates(term):
        pair = (candidate, reading)
        if candidate and pair not in pairs:
            pairs.append(pair)
    return pairs


def zh_speakable(term: str, reading: str) -> str | None:
    """What Google TTS speaks for a Chinese pair: the characters, never the pinyin.

    The reading slot holds tone-marked pinyin, which a zh-CN voice reads as
    Latin letters; the term (simplified or traditional variant) is what it can
    pronounce. The cache stem still carries the pinyin, keeping homographs apart.

    Known limitation, accepted: handing the voice characters lets it GUESS the
    reading of a polyphone (行 háng/xíng, 长 cháng/zhǎng, 还 hái/huán) — exactly
    the trade ``GoogleTranslateAudioFetcher.fetch``'s docstring refuses for
    Japanese kanji. Multi-character words are mostly unambiguous; single-character
    polyphones may be spoken wrong. This deviates from spec S1's generic
    ``reading or term`` on purpose; a new language copies it only after checking
    what its reading slot holds.
    """
    return term or None


ZH_AUDIO = AudioDefaults(
    gtts_lang="zh-CN",
    cache_stem_prefix="googletts_zh",
    sentence_cache_stem_prefix="sentencetts_zh",
    custom_fetcher_language="zh",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=zh_audio_candidates,
    speakable=zh_speakable,
)
